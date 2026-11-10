package br.uece.eventhub.registration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = "policy.waitlist-enabled=true")
@ActiveProfiles("test")
class WaitlistContractTest {
  private static final UUID ACTIVITY_ID = UUID.fromString("50000000-0000-0000-0000-000000000001");
  private static final UUID ORGANIZATION_ID =
      UUID.fromString("50000000-0000-0000-0000-000000000002");
  private static final int CAPACITY = 1;

  @Autowired private TestRestTemplate client;
  @Autowired private JdbcTemplate jdbc;
  @Autowired private RegistrationService service;
  @MockBean private IdentityClient identityClient;
  @MockBean private EventManagementClient eventManagementClient;
  @MockBean private RabbitTemplate rabbitTemplate;

  @BeforeEach
  void setUp() {
    jdbc.update("DELETE FROM billing_outbox");
    jdbc.update("DELETE FROM command_receipt");
    jdbc.update("DELETE FROM waitlist_entry");
    jdbc.update("DELETE FROM registration");
    jdbc.update("DELETE FROM activity_ledger");
    when(identityClient.validate(anyString(), anyString()))
        .thenAnswer(
            invocation -> {
              String token = invocation.getArgument(0);
              return new Principal(
                  UUID.nameUUIDFromBytes(token.getBytes(StandardCharsets.UTF_8)),
                  Principal.Role.PARTICIPANT,
                  null);
            });
    when(eventManagementClient.activateOrValidateAdmission(any(UUID.class), anyString()))
        .thenAnswer(invocation -> admission(invocation.getArgument(0)));
  }

  @Test
  void concurrentFullCapacityRequestsQueueInOrderAndCancellationOffersTheNextParticipant()
      throws Exception {
    ResponseEntity<JsonNode> holder = create("holder");
    UUID holderId = UUID.fromString(holder.getBody().path("id").asText());

    List<ResponseEntity<JsonNode>> responses = new ArrayList<>();
    try (var executor = Executors.newFixedThreadPool(6)) {
      List<Callable<ResponseEntity<JsonNode>>> requests = new ArrayList<>();
      for (int index = 0; index < 6; index++) {
        String token = "waiter-" + index;
        requests.add(() -> create(token));
      }
      for (var future : executor.invokeAll(requests)) {
        responses.add(future.get(10, TimeUnit.SECONDS));
      }
    }

    assertThat(responses)
        .allSatisfy(
            response -> {
              assertThat(response.getStatusCode().value()).isEqualTo(201);
              assertThat(response.getBody().path("status").asText()).isEqualTo("WAITLISTED");
              assertThat(response.getBody().path("waitlistSequence").asLong()).isPositive();
            });
    List<WaitlistedRecord> queue = waitlist();
    assertThat(queue).hasSize(6);
    assertThat(queue.stream().map(WaitlistedRecord::sequence).toList())
        .isSorted()
        .doesNotHaveDuplicates();
    assertThat(queue).allSatisfy(entry -> assertThat(entry.status()).isEqualTo("WAITLISTED"));

    cancel(holderId, "holder", "holder-cancel");
    WaitlistedRecord first = waitlist().getFirst();
    assertThat(first.status()).isEqualTo("OFFERED");
    assertThat(occupied()).isEqualTo(CAPACITY);

    cancel(first.id(), tokenFor(first.participantId()), "first-offer-cancel");
    assertThat(waitlist().getFirst().status()).isEqualTo("OFFERED");
    assertThat(waitlist().getFirst().id()).isNotEqualTo(first.id());
    assertThat(occupied()).isEqualTo(CAPACITY);
  }

  @Test
  void offerExpiryPromotesNextEntryOnceAndFreeOfferCanBeAccepted() {
    ResponseEntity<JsonNode> holder = create("expiry-holder");
    ResponseEntity<JsonNode> firstWaiter = create("expiry-waiter-1");
    ResponseEntity<JsonNode> secondWaiter = create("expiry-waiter-2");
    UUID holderId = UUID.fromString(holder.getBody().path("id").asText());
    UUID firstId = UUID.fromString(firstWaiter.getBody().path("id").asText());
    UUID secondId = UUID.fromString(secondWaiter.getBody().path("id").asText());

    cancel(holderId, "expiry-holder", "expiry-holder-cancel");
    assertThat(status(firstId)).isEqualTo("OFFERED");
    jdbc.update(
        "UPDATE registration SET offer_expires_at = ? WHERE id = ?",
        OffsetDateTime.now(ZoneOffset.UTC).minusMinutes(1),
        firstId);

    service.expireOffers();
    service.expireOffers();

    assertThat(status(firstId)).isEqualTo("EXPIRED");
    assertThat(status(secondId)).isEqualTo("OFFERED");
    assertThat(occupied()).isEqualTo(CAPACITY);

    ResponseEntity<JsonNode> accepted =
        client.exchange(
            "/registrations/{id}/accept-offer",
            HttpMethod.POST,
            request("expiry-waiter-2", "accept-second-offer", null),
            JsonNode.class,
            secondId);
    ResponseEntity<JsonNode> replayed =
        client.exchange(
            "/registrations/{id}/accept-offer",
            HttpMethod.POST,
            request("expiry-waiter-2", "accept-second-offer", null),
            JsonNode.class,
            secondId);

    assertThat(accepted.getStatusCode().value()).isEqualTo(200);
    assertThat(accepted.getBody().path("status").asText()).isEqualTo("CONFIRMED");
    assertThat(accepted.getBody().path("waitlistSequence").asLong()).isPositive();
    assertThat(replayed.getBody().path("id").asText()).isEqualTo(secondId.toString());
    assertThat(occupied()).isEqualTo(CAPACITY);
  }

  @Test
  void acceptingPaidOfferStartsPaymentReservationWithoutReleasingTheSlot() {
    OffsetDateTime startsAt = OffsetDateTime.now(ZoneOffset.UTC).plusDays(1);
    when(eventManagementClient.activateOrValidateAdmission(any(UUID.class), anyString()))
        .thenReturn(
            new AdmissionSnapshot(
                UUID.nameUUIDFromBytes("paid-snapshot".getBytes(StandardCharsets.UTF_8)),
                1,
                CAPACITY,
                ORGANIZATION_ID,
                2500,
                "USD",
                startsAt,
                startsAt.plusHours(1)));
    ResponseEntity<JsonNode> holder = create("paid-holder");
    ResponseEntity<JsonNode> waiter = create("paid-waiter");
    UUID holderId = UUID.fromString(holder.getBody().path("id").asText());
    UUID waiterId = UUID.fromString(waiter.getBody().path("id").asText());

    cancel(holderId, "paid-holder", "paid-holder-cancel");
    ResponseEntity<JsonNode> accepted =
        client.exchange(
            "/registrations/{id}/accept-offer",
            HttpMethod.POST,
            request("paid-waiter", "accept-paid-offer", null),
            JsonNode.class,
            waiterId);
    ResponseEntity<JsonNode> replayed =
        client.exchange(
            "/registrations/{id}/accept-offer",
            HttpMethod.POST,
            request("paid-waiter", "accept-paid-offer", null),
            JsonNode.class,
            waiterId);

    assertThat(accepted.getStatusCode().value()).isEqualTo(200);
    assertThat(accepted.getBody().path("status").asText()).isEqualTo("PENDING_PAYMENT");
    assertThat(accepted.getBody().path("expiresAt").asText()).isNotBlank();
    assertThat(accepted.getBody().path("offerExpiresAt").isNull()).isTrue();
    assertThat(replayed.getBody().path("id").asText()).isEqualTo(waiterId.toString());
    assertThat(
            jdbc.queryForObject(
                "SELECT COUNT(*) FROM billing_outbox WHERE registration_id = ? "
                    + "AND routing_key = 'billing.requested'",
                Integer.class,
                waiterId))
        .isEqualTo(1);
    assertThat(
            jdbc.queryForObject(
                "SELECT COUNT(*) FROM billing_outbox WHERE registration_id = ? "
                    + "AND routing_key = 'registration.changed'",
                Integer.class,
                waiterId))
        .isEqualTo(3);
    assertThat(occupied()).isEqualTo(CAPACITY);
  }

  private ResponseEntity<JsonNode> create(String token) {
    return client.exchange(
        "/registrations",
        HttpMethod.POST,
        request(token, "create-" + token, "{\"activityId\":\"" + ACTIVITY_ID + "\"}"),
        JsonNode.class);
  }

  private void cancel(UUID registrationId, String token, String key) {
    ResponseEntity<JsonNode> response =
        client.exchange(
            "/registrations/{id}/cancel",
            HttpMethod.POST,
            request(token, key, null),
            JsonNode.class,
            registrationId);
    assertThat(response.getStatusCode().value()).isEqualTo(200);
  }

  private HttpEntity<String> request(String token, String key, String body) {
    HttpHeaders headers = new HttpHeaders();
    headers.setBearerAuth(token);
    headers.setContentType(MediaType.APPLICATION_JSON);
    if (key != null) {
      headers.set("Idempotency-Key", key);
    }
    return new HttpEntity<>(body, headers);
  }

  private AdmissionSnapshot admission(UUID activityId) {
    OffsetDateTime startsAt = OffsetDateTime.now(ZoneOffset.UTC).plusDays(1);
    return new AdmissionSnapshot(
        UUID.nameUUIDFromBytes(("snapshot-" + activityId).getBytes(StandardCharsets.UTF_8)),
        1,
        CAPACITY,
        ORGANIZATION_ID,
        0,
        "USD",
        startsAt,
        startsAt.plusHours(1));
  }

  private List<WaitlistedRecord> waitlist() {
    return jdbc.query(
        "SELECT w.registration_id, w.sequence, r.participant_id, r.status "
            + "FROM waitlist_entry w JOIN registration r ON r.id = w.registration_id "
            + "WHERE w.activity_id = ? AND r.status IN ('WAITLISTED', 'OFFERED') "
            + "ORDER BY w.sequence",
        (row, rowNumber) ->
            new WaitlistedRecord(
                row.getObject("registration_id", UUID.class),
                row.getLong("sequence"),
                row.getObject("participant_id", UUID.class),
                row.getString("status")),
        ACTIVITY_ID);
  }

  private String status(UUID registrationId) {
    return jdbc.queryForObject(
        "SELECT status FROM registration WHERE id = ?", String.class, registrationId);
  }

  private String tokenFor(UUID participantId) {
    return List.of("waiter-0", "waiter-1", "waiter-2", "waiter-3", "waiter-4", "waiter-5").stream()
        .filter(
            token ->
                UUID.nameUUIDFromBytes(token.getBytes(StandardCharsets.UTF_8))
                    .equals(participantId))
        .findFirst()
        .orElseThrow();
  }

  private int occupied() {
    return jdbc.queryForObject(
        "SELECT occupied FROM activity_ledger WHERE activity_id = ?", Integer.class, ACTIVITY_ID);
  }

  private record WaitlistedRecord(UUID id, long sequence, UUID participantId, String status) {}
}
