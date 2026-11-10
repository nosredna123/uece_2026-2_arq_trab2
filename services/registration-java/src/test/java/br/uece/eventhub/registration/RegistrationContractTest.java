package br.uece.eventhub.registration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
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
import org.springframework.web.client.ResourceAccessException;

@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = "policy.waitlist-enabled=false")
@ActiveProfiles("test")
class RegistrationContractTest {
  private static final UUID ACTIVITY_ID = UUID.fromString("10000000-0000-0000-0000-000000000001");
  private static final UUID OTHER_ACTIVITY_ID =
      UUID.fromString("10000000-0000-0000-0000-000000000002");
  private static final UUID ORGANIZATION_ID =
      UUID.fromString("20000000-0000-0000-0000-000000000001");
  private static final UUID PARTICIPANT_ID =
      UUID.fromString("30000000-0000-0000-0000-000000000001");
  private static final UUID OTHER_PARTICIPANT_ID =
      UUID.fromString("30000000-0000-0000-0000-000000000002");

  @Autowired private TestRestTemplate client;
  @Autowired private JdbcTemplate jdbc;
  @MockBean private IdentityClient identityClient;
  @MockBean private EventManagementClient eventManagementClient;

  @BeforeEach
  void setUp() {
    jdbc.update("DELETE FROM command_receipt");
    jdbc.update("DELETE FROM registration");
    jdbc.update("DELETE FROM activity_ledger");
    when(identityClient.validate(eq("participant-token"), anyString()))
        .thenReturn(new Principal(PARTICIPANT_ID, Principal.Role.PARTICIPANT, null));
    when(identityClient.validate(eq("other-participant-token"), anyString()))
        .thenReturn(new Principal(OTHER_PARTICIPANT_ID, Principal.Role.PARTICIPANT, null));
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 0));
    when(eventManagementClient.activateOrValidateAdmission(eq(OTHER_ACTIVITY_ID), anyString()))
        .thenReturn(admission(OTHER_ACTIVITY_ID, 2, 0));
  }

  @Test
  void confirmsFreeRegistrationAndReplaysSameIdempotentResponse() {
    ResponseEntity<JsonNode> first = create(ACTIVITY_ID, "free-registration", "participant-token");
    ResponseEntity<JsonNode> retry = create(ACTIVITY_ID, "free-registration", "participant-token");

    assertThat(first.getStatusCode().value()).isEqualTo(201);
    assertThat(first.getBody().path("status").asText()).isEqualTo("CONFIRMED");
    assertThat(first.getBody().path("price").path("amountMinor").asLong()).isZero();
    assertThat(retry.getStatusCode().value()).isEqualTo(201);
    assertThat(retry.getBody().path("id").asText()).isEqualTo(first.getBody().path("id").asText());
    assertThat(occupied(ACTIVITY_ID)).isEqualTo(1);
  }

  @Test
  void idempotentRetryDoesNotDependOnEventManagementAvailability() {
    ResponseEntity<JsonNode> first = create(ACTIVITY_ID, "dependency-retry", "participant-token");
    doThrow(new ResourceAccessException("synthetic outage"))
        .when(eventManagementClient)
        .activateOrValidateAdmission(eq(ACTIVITY_ID), anyString());

    ResponseEntity<JsonNode> retry = create(ACTIVITY_ID, "dependency-retry", "participant-token");

    assertThat(first.getStatusCode().value()).isEqualTo(201);
    assertThat(retry.getStatusCode().value()).isEqualTo(201);
    assertThat(retry.getBody().path("id").asText()).isEqualTo(first.getBody().path("id").asText());
    assertThat(occupied(ACTIVITY_ID)).isEqualTo(1);
  }

  @Test
  void rejectsIdempotencyKeyReuseWithDifferentPayload() {
    ResponseEntity<JsonNode> first = create(ACTIVITY_ID, "reused-key", "participant-token");
    ResponseEntity<JsonNode> changed = create(OTHER_ACTIVITY_ID, "reused-key", "participant-token");

    assertThat(first.getStatusCode().value()).isEqualTo(201);
    assertThat(changed.getStatusCode().value()).isEqualTo(409);
    assertThat(occupied(ACTIVITY_ID)).isEqualTo(1);
    assertThat(occupied(OTHER_ACTIVITY_ID)).isZero();
  }

  @Test
  void cancellationReleasesFreeRegistrationOnlyOnceAndConcealsForeignState() {
    ResponseEntity<JsonNode> created = create(ACTIVITY_ID, "registration-key", "participant-token");
    UUID registrationId = UUID.fromString(created.getBody().path("id").asText());
    ResponseEntity<JsonNode> ownState =
        client.exchange(
            "/registrations/{id}",
            HttpMethod.GET,
            request("participant-token", null, null),
            JsonNode.class,
            registrationId);
    ResponseEntity<JsonNode> ownList =
        client.exchange(
            "/registrations",
            HttpMethod.GET,
            request("participant-token", null, null),
            JsonNode.class);

    ResponseEntity<JsonNode> cancelled =
        client.exchange(
            "/registrations/{id}/cancel",
            HttpMethod.POST,
            request("participant-token", "cancel-key", null),
            JsonNode.class,
            registrationId);
    ResponseEntity<JsonNode> retried =
        client.exchange(
            "/registrations/{id}/cancel",
            HttpMethod.POST,
            request("participant-token", "cancel-key", null),
            JsonNode.class,
            registrationId);
    ResponseEntity<JsonNode> foreign =
        client.exchange(
            "/registrations/{id}",
            HttpMethod.GET,
            request("other-participant-token", null, null),
            JsonNode.class,
            registrationId);

    assertThat(cancelled.getStatusCode().value()).isEqualTo(200);
    assertThat(cancelled.getBody().path("status").asText()).isEqualTo("CANCELLED");
    assertThat(ownState.getStatusCode().value()).isEqualTo(200);
    assertThat(ownState.getBody().path("status").asText()).isEqualTo("CONFIRMED");
    assertThat(ownList.getStatusCode().value()).isEqualTo(200);
    assertThat(ownList.getBody()).hasSize(1);
    assertThat(retried.getBody().path("id").asText()).isEqualTo(registrationId.toString());
    assertThat(occupied(ACTIVITY_ID)).isZero();
    assertThat(foreign.getStatusCode().value()).isEqualTo(404);
  }

  @Test
  void availabilityDoesNotCreateOrFreezeTheLocalLedger() {
    when(eventManagementClient.getPublishedActivity(eq(ACTIVITY_ID), anyString()))
        .thenReturn(new PublicActivity(ACTIVITY_ID, 4));

    ResponseEntity<JsonNode> response =
        client.exchange(
            "/activities/{activityId}/availability",
            HttpMethod.GET,
            HttpEntity.EMPTY,
            JsonNode.class,
            ACTIVITY_ID);

    assertThat(response.getStatusCode().value()).isEqualTo(200);
    assertThat(response.getBody().path("capacity").asInt()).isEqualTo(4);
    assertThat(response.getBody().path("occupied").asInt()).isZero();
    assertThat(response.getBody().path("available").asInt()).isEqualTo(4);
    assertThat(
            jdbc.queryForObject(
                "SELECT COUNT(*) FROM activity_ledger WHERE activity_id = ?",
                Integer.class,
                ACTIVITY_ID))
        .isZero();
  }

  @Test
  void paidRegistrationReservesCapacityWithoutConfirmingPayment() {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 2500));

    ResponseEntity<JsonNode> created =
        create(ACTIVITY_ID, "paid-registration", "participant-token");

    assertThat(created.getStatusCode().value()).isEqualTo(201);
    assertThat(created.getBody().path("status").asText()).isEqualTo("PENDING_PAYMENT");
    assertThat(created.getBody().path("expiresAt").isTextual()).isTrue();
    assertThat(occupied(ACTIVITY_ID)).isEqualTo(1);
  }

  @Test
  void rejectsACompetingLastSlotRequestWithoutOverbooking() {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 0));
    ResponseEntity<JsonNode> first = create(ACTIVITY_ID, "first", "participant-token");
    ResponseEntity<JsonNode> second = create(ACTIVITY_ID, "second", "other-participant-token");

    assertThat(first.getStatusCode().value()).isEqualTo(201);
    assertThat(second.getStatusCode().value()).isEqualTo(409);
    assertThat(occupied(ACTIVITY_ID)).isEqualTo(1);
  }

  private ResponseEntity<JsonNode> create(UUID activityId, String idempotencyKey, String token) {
    return client.exchange(
        "/registrations",
        HttpMethod.POST,
        request(token, idempotencyKey, "{\"activityId\":\"" + activityId + "\"}"),
        JsonNode.class);
  }

  private HttpEntity<String> request(String token, String idempotencyKey, String body) {
    HttpHeaders headers = new HttpHeaders();
    headers.setBearerAuth(token);
    headers.setContentType(MediaType.APPLICATION_JSON);
    if (idempotencyKey != null) {
      headers.set("Idempotency-Key", idempotencyKey);
    }
    return new HttpEntity<>(body, headers);
  }

  private int occupied(UUID activityId) {
    return jdbc.query(
        "SELECT occupied FROM activity_ledger WHERE activity_id = ?",
        resultSet -> resultSet.next() ? resultSet.getInt("occupied") : 0,
        activityId);
  }

  private AdmissionSnapshot admission(UUID activityId, int capacity, long amountMinor) {
    return new AdmissionSnapshot(
        UUID.nameUUIDFromBytes(("snapshot-" + activityId).getBytes()),
        1,
        capacity,
        ORGANIZATION_ID,
        amountMinor,
        "USD");
  }
}
