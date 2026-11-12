package br.uece.eventhub.registration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.Channel;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.connection.CorrelationData;
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
  @Autowired private ObjectMapper objectMapper;
  @Autowired private JdbcTemplate jdbc;
  @Autowired private BillingOutboxPublisher outboxPublisher;
  @Autowired private BillingResultConsumer billingResultConsumer;
  @Autowired private RegistrationService service;
  @MockBean private IdentityClient identityClient;
  @MockBean private EventManagementClient eventManagementClient;
  @MockBean private RabbitTemplate rabbitTemplate;

  @BeforeEach
  void setUp() {
    jdbc.update("DELETE FROM billing_result_inbox");
    jdbc.update("DELETE FROM billing_outbox");
    jdbc.update("DELETE FROM waitlist_entry");
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
  void invalidCorrelationIdIsRejectedAndReplacedForTheError() {
    HttpHeaders headers = new HttpHeaders();
    headers.set("X-Correlation-ID", "not-a-uuid");

    ResponseEntity<JsonNode> response =
        client.exchange(
            "/registrations", HttpMethod.GET, new HttpEntity<>(headers), JsonNode.class);

    assertThat(response.getStatusCode().value()).isEqualTo(400);
    assertThat(response.getHeaders().getFirst("X-Correlation-ID")).isNotBlank();
    assertThat(response.getBody().path("correlationId").asText())
        .isEqualTo(response.getHeaders().getFirst("X-Correlation-ID"));
  }

  @Test
  void registrationCorrelationIdReachesDependenciesAndEmittedMessage() throws Exception {
    UUID correlationId = UUID.fromString("90000000-0000-0000-0000-000000000001");
    HttpHeaders headers = new HttpHeaders();
    headers.setBearerAuth("participant-token");
    headers.setContentType(MediaType.APPLICATION_JSON);
    headers.set("Idempotency-Key", "correlation-flow");
    headers.set("X-Correlation-ID", correlationId.toString());

    ResponseEntity<JsonNode> response =
        client.exchange(
            "/registrations",
            HttpMethod.POST,
            new HttpEntity<>("{\"activityId\":\"" + ACTIVITY_ID + "\"}", headers),
            JsonNode.class);

    UUID registrationId = UUID.fromString(response.getBody().path("id").asText());
    String snapshotBody =
        jdbc.queryForObject(
            "SELECT body FROM billing_outbox WHERE registration_id = ? "
                + "AND routing_key = 'registration.changed'",
            String.class,
            registrationId);
    JsonNode snapshot = objectMapper.readTree(snapshotBody);

    assertThat(response.getStatusCode().value()).isEqualTo(201);
    assertThat(response.getHeaders().getFirst("X-Correlation-ID"))
        .isEqualTo(correlationId.toString());
    verify(identityClient).validate("participant-token", correlationId.toString());
    verify(eventManagementClient)
        .activateOrValidateAdmission(ACTIVITY_ID, correlationId.toString());
    assertThat(snapshot.path("correlationId").asText()).isEqualTo(correlationId.toString());
    assertThat(snapshot.path("data").path("registrationId").asText())
        .isEqualTo(registrationId.toString());
  }

  @Test
  void confirmsFreeRegistrationAndReplaysSameIdempotentResponse() throws Exception {
    ResponseEntity<JsonNode> first = create(ACTIVITY_ID, "free-registration", "participant-token");
    ResponseEntity<JsonNode> retry = create(ACTIVITY_ID, "free-registration", "participant-token");
    UUID registrationId = UUID.fromString(first.getBody().path("id").asText());
    String snapshotBody =
        jdbc.queryForObject(
            "SELECT body FROM billing_outbox WHERE registration_id = ? "
                + "AND routing_key = 'registration.changed'",
            String.class,
            registrationId);
    JsonNode snapshot = new ObjectMapper().readTree(snapshotBody);

    assertThat(first.getStatusCode().value()).isEqualTo(201);
    assertThat(first.getBody().path("status").asText()).isEqualTo("CONFIRMED");
    assertThat(first.getBody().path("price").path("amountMinor").asLong()).isZero();
    assertThat(retry.getStatusCode().value()).isEqualTo(201);
    assertThat(retry.getBody().path("id").asText()).isEqualTo(first.getBody().path("id").asText());
    assertThat(occupied(ACTIVITY_ID)).isEqualTo(1);
    assertThat(outboxCount()).isZero();
    assertThat(registrationSnapshotCount(registrationId)).isEqualTo(1);
    assertThat(snapshot.path("eventType").asText()).isEqualTo("registration.changed");
    assertThat(snapshot.path("aggregateVersion").asLong()).isEqualTo(1);
    assertThat(snapshot.path("data").path("status").asText()).isEqualTo("CONFIRMED");
    assertThat(snapshot.path("data").path("activityStartsAt").asText()).isNotBlank();
    assertThat(snapshot.path("data").path("activityEndsAt").asText()).isNotBlank();
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
  void paidRegistrationWritesOneStableBillingRequestBeforeIdempotentReplay() throws Exception {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 2500));

    ResponseEntity<JsonNode> first = create(ACTIVITY_ID, "paid-outbox", "participant-token");
    ResponseEntity<JsonNode> replay = create(ACTIVITY_ID, "paid-outbox", "participant-token");
    UUID registrationId = UUID.fromString(first.getBody().path("id").asText());
    String body =
        jdbc.queryForObject(
            "SELECT body FROM billing_outbox WHERE registration_id = ? "
                + "AND routing_key = 'billing.requested'",
            String.class,
            registrationId);
    JsonNode request = new com.fasterxml.jackson.databind.ObjectMapper().readTree(body);

    assertThat(first.getStatusCode().value()).isEqualTo(201);
    assertThat(replay.getBody().path("id").asText()).isEqualTo(registrationId.toString());
    assertThat(outboxCount()).isEqualTo(1);
    assertThat(request.path("eventType").asText()).isEqualTo("billing.requested");
    assertThat(request.path("producer").asText()).isEqualTo("registration");
    assertThat(request.path("aggregateId").asText()).isEqualTo(registrationId.toString());
    assertThat(request.path("data").path("operationId").asText())
        .isEqualTo(registrationId.toString());
    assertThat(request.path("data").path("registrationId").asText())
        .isEqualTo(registrationId.toString());
    assertThat(request.path("data").path("amountMinor").asLong()).isEqualTo(2500);
    assertThat(request.path("data").path("expiresAt").asText()).isNotBlank();
    assertThat(request.path("correlationId").asText()).isNotBlank();
  }

  @Test
  void confirmedOutboxPublicationUsesPersistentMessageIdentity() {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 2500));
    ResponseEntity<JsonNode> created = create(ACTIVITY_ID, "publish-outbox", "participant-token");
    UUID registrationId = UUID.fromString(created.getBody().path("id").asText());
    doAnswer(
            invocation -> {
              CorrelationData correlationData = invocation.getArgument(3);
              correlationData.getFuture().complete(new CorrelationData.Confirm(true, null));
              return null;
            })
        .when(rabbitTemplate)
        .send(eq("eventhub.v1"), anyString(), any(Message.class), any(CorrelationData.class));

    int published = outboxPublisher.publishPending();
    ArgumentCaptor<Message> message = ArgumentCaptor.forClass(Message.class);
    verify(rabbitTemplate)
        .send(
            eq("eventhub.v1"),
            eq("billing.requested"),
            message.capture(),
            any(CorrelationData.class));

    assertThat(published).isEqualTo(2);
    assertThat(message.getValue().getMessageProperties().getDeliveryMode())
        .isEqualTo(MessageDeliveryMode.PERSISTENT);
    assertThat(message.getValue().getMessageProperties().getMessageId()).isNotBlank();
    assertThat(message.getValue().getMessageProperties().getCorrelationId()).isNotBlank();
    assertThat(outboxCount()).isZero();
    assertThat(
            jdbc.queryForObject(
                "SELECT COUNT(*) FROM billing_outbox "
                    + "WHERE registration_id = ? AND published_at IS NOT NULL",
                Integer.class,
                registrationId))
        .isEqualTo(2);
  }

  @Test
  void unconfirmedOutboxPublicationRemainsRetryable() {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 2500));
    create(ACTIVITY_ID, "unconfirmed-outbox", "participant-token");
    doAnswer(
            invocation -> {
              CorrelationData correlationData = invocation.getArgument(3);
              correlationData.getFuture().complete(new CorrelationData.Confirm(false, "nack"));
              return null;
            })
        .when(rabbitTemplate)
        .send(eq("eventhub.v1"), anyString(), any(Message.class), any(CorrelationData.class));

    assertThat(outboxPublisher.publishPending()).isZero();
    assertThat(outboxCount()).isEqualTo(1);
  }

  @Test
  void nackedMessageInABatchDoesNotHoldBackConfirmedOnes() {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 2500));
    create(ACTIVITY_ID, "partially-confirmed-outbox", "participant-token");
    doAnswer(
            invocation -> {
              String routingKey = invocation.getArgument(1);
              CorrelationData correlationData = invocation.getArgument(3);
              boolean ack = !"billing.requested".equals(routingKey);
              correlationData
                  .getFuture()
                  .complete(new CorrelationData.Confirm(ack, ack ? null : "nack"));
              return null;
            })
        .when(rabbitTemplate)
        .send(eq("eventhub.v1"), anyString(), any(Message.class), any(CorrelationData.class));

    assertThat(outboxPublisher.publishPending()).isEqualTo(1);
    assertThat(outboxCount()).isEqualTo(1);
    assertThat(
            jdbc.queryForObject(
                "SELECT COUNT(*) FROM billing_outbox WHERE routing_key = 'billing.requested' "
                    + "AND published_at IS NULL AND available_at > CURRENT_TIMESTAMP",
                Integer.class))
        .isEqualTo(1);
  }

  @Test
  void approvalConfirmsPaymentAndDuplicateResultsDoNotChangeCapacity() throws Exception {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 2500));
    ResponseEntity<JsonNode> created = create(ACTIVITY_ID, "approved-payment", "participant-token");
    UUID registrationId = UUID.fromString(created.getBody().path("id").asText());
    BillingResult result = billingResult(registrationId, "APPROVED", UUID.randomUUID());

    service.applyBillingResult(result);
    service.applyBillingResult(result);

    assertThat(status(registrationId)).isEqualTo("CONFIRMED");
    assertThat(occupied(ACTIVITY_ID)).isEqualTo(1);
    assertThat(inboxDisposition(result.eventId())).isEqualTo("APPLIED_APPROVAL");
    assertThat(registrationSnapshotCount(registrationId)).isEqualTo(2);
    JsonNode latestSnapshot =
        new ObjectMapper().readTree(latestRegistrationSnapshot(registrationId));
    assertThat(latestSnapshot.path("aggregateVersion").asLong()).isEqualTo(2);
    assertThat(latestSnapshot.path("data").path("status").asText()).isEqualTo("CONFIRMED");
  }

  @Test
  void refusalReleasesReservationOnceAndPermitsAnotherParticipant() {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 2500));
    ResponseEntity<JsonNode> created = create(ACTIVITY_ID, "refused-payment", "participant-token");
    UUID registrationId = UUID.fromString(created.getBody().path("id").asText());
    BillingResult result = billingResult(registrationId, "REFUSED", UUID.randomUUID());

    service.applyBillingResult(result);
    service.applyBillingResult(result);
    service.applyBillingResult(billingResult(registrationId, "REFUSED", UUID.randomUUID()));

    assertThat(status(registrationId)).isEqualTo("REFUSED");
    assertThat(occupied(ACTIVITY_ID)).isZero();
    assertThat(registrationSnapshotCount(registrationId)).isEqualTo(2);
    ResponseEntity<JsonNode> next = create(ACTIVITY_ID, "after-refusal", "other-participant-token");
    assertThat(next.getStatusCode().value()).isEqualTo(201);
    assertThat(occupied(ACTIVITY_ID)).isEqualTo(1);
  }

  @Test
  void expiredReservationReleasesOnceAndLateApprovalIsRecordedWithoutReconfirmation()
      throws Exception {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 2500));
    ResponseEntity<JsonNode> created = create(ACTIVITY_ID, "expired-payment", "participant-token");
    UUID registrationId = UUID.fromString(created.getBody().path("id").asText());
    jdbc.update(
        "UPDATE registration SET expires_at = ? WHERE id = ?",
        OffsetDateTime.now(ZoneOffset.UTC).minusMinutes(1),
        registrationId);

    service.expireReservations();
    service.expireReservations();
    BillingResult lateApproval = billingResult(registrationId, "APPROVED", UUID.randomUUID());
    service.applyBillingResult(lateApproval);

    assertThat(status(registrationId)).isEqualTo("EXPIRED");
    assertThat(occupied(ACTIVITY_ID)).isZero();
    assertThat(inboxDisposition(lateApproval.eventId())).isEqualTo("LATE_APPROVAL");
    assertThat(registrationSnapshotCount(registrationId)).isEqualTo(2);
    JsonNode latestSnapshot =
        new ObjectMapper().readTree(latestRegistrationSnapshot(registrationId));
    assertThat(latestSnapshot.path("data").path("status").asText()).isEqualTo("EXPIRED");
    assertThat(
            jdbc.queryForObject(
                "SELECT body FROM billing_result_inbox WHERE event_id = ?",
                String.class,
                lateApproval.eventId()))
        .contains("\"status\":\"APPROVED\"");
    assertThat(outboxCount()).isZero();
    ResponseEntity<JsonNode> next = create(ACTIVITY_ID, "after-expiry", "other-participant-token");
    assertThat(next.getStatusCode().value()).isEqualTo(201);
    assertThat(occupied(ACTIVITY_ID)).isEqualTo(1);
  }

  @Test
  void rejectsBillingResultForAnotherPaymentAttempt() {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 2500));
    ResponseEntity<JsonNode> created =
        create(ACTIVITY_ID, "wrong-payment-attempt", "participant-token");
    UUID registrationId = UUID.fromString(created.getBody().path("id").asText());
    BillingResult result = billingResult(registrationId, "APPROVED", UUID.randomUUID());
    BillingResult mismatchedAttempt =
        new BillingResult(
            result.eventId(),
            result.eventType(),
            result.schemaVersion(),
            result.aggregateId(),
            result.aggregateVersion(),
            result.correlationId(),
            result.occurredAt(),
            result.producer(),
            new BillingResultData(
                result.data().operationId(),
                UUID.randomUUID(),
                result.data().kind(),
                result.data().registrationId(),
                result.data().activityId(),
                result.data().organizationId(),
                result.data().status(),
                result.data().amountMinor(),
                result.data().currency()));

    assertThatThrownBy(() -> service.applyBillingResult(mismatchedAttempt))
        .isInstanceOf(ApiException.class)
        .hasMessageContaining("attempt does not match");
    assertThat(status(registrationId)).isEqualTo("PENDING_PAYMENT");
    assertThat(occupied(ACTIVITY_ID)).isEqualTo(1);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM billing_result_inbox", Integer.class))
        .isZero();
  }

  @Test
  void quarantinesBillingMessageWhenTransportIdentityDoesNotMatchEnvelope() throws Exception {
    when(eventManagementClient.activateOrValidateAdmission(eq(ACTIVITY_ID), anyString()))
        .thenReturn(admission(ACTIVITY_ID, 1, 2500));
    ResponseEntity<JsonNode> created =
        create(ACTIVITY_ID, "transport-identity-mismatch", "participant-token");
    UUID registrationId = UUID.fromString(created.getBody().path("id").asText());
    BillingResult result = billingResult(registrationId, "APPROVED", UUID.randomUUID());
    MessageProperties sourceProperties = new MessageProperties();
    sourceProperties.setDeliveryTag(24);
    sourceProperties.setMessageId(UUID.randomUUID().toString());
    sourceProperties.setCorrelationId(result.correlationId().toString());
    Message source = new Message(objectMapper.writeValueAsBytes(result), sourceProperties);
    Channel channel = org.mockito.Mockito.mock(Channel.class);
    doAnswer(
            invocation -> {
              CorrelationData correlationData = invocation.getArgument(3);
              correlationData.getFuture().complete(new CorrelationData.Confirm(true, null));
              return null;
            })
        .when(rabbitTemplate)
        .send(
            eq("eventhub.v1"),
            eq("registration.v1.quarantine"),
            any(Message.class),
            any(CorrelationData.class));

    billingResultConsumer.consume(source, channel);

    ArgumentCaptor<Message> quarantined = ArgumentCaptor.forClass(Message.class);
    verify(rabbitTemplate)
        .send(
            eq("eventhub.v1"),
            eq("registration.v1.quarantine"),
            quarantined.capture(),
            any(CorrelationData.class));
    assertThat(quarantined.getValue().getMessageProperties().getHeaders())
        .containsEntry("x-error-code", "INVALID_BILLING_RESULT");
    verify(channel).basicAck(24, false);
  }

  @Test
  void quarantinesInvalidBillingMessageBeforeAcknowledgingIt() throws Exception {
    MessageProperties sourceProperties = new MessageProperties();
    sourceProperties.setDeliveryTag(23);
    sourceProperties.setMessageId("invalid-billing-result");
    Message source = new Message("{".getBytes(StandardCharsets.UTF_8), sourceProperties);
    Channel channel = org.mockito.Mockito.mock(Channel.class);
    doAnswer(
            invocation -> {
              CorrelationData correlationData = invocation.getArgument(3);
              correlationData.getFuture().complete(new CorrelationData.Confirm(true, null));
              return null;
            })
        .when(rabbitTemplate)
        .send(
            eq("eventhub.v1"),
            eq("registration.v1.quarantine"),
            any(Message.class),
            any(CorrelationData.class));

    billingResultConsumer.consume(source, channel);

    ArgumentCaptor<Message> quarantined = ArgumentCaptor.forClass(Message.class);
    verify(rabbitTemplate)
        .send(
            eq("eventhub.v1"),
            eq("registration.v1.quarantine"),
            quarantined.capture(),
            any(CorrelationData.class));
    assertThat(quarantined.getValue().getMessageProperties().getMessageId())
        .isEqualTo("invalid-billing-result");
    assertThat(quarantined.getValue().getMessageProperties().getHeaders())
        .containsEntry("x-error-code", "INVALID_BILLING_RESULT");
    verify(channel).basicAck(23, false);
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

  private int outboxCount() {
    return jdbc.queryForObject(
        "SELECT COUNT(*) FROM billing_outbox WHERE routing_key = 'billing.requested' "
            + "AND published_at IS NULL AND suppressed_at IS NULL",
        Integer.class);
  }

  private int registrationSnapshotCount(UUID registrationId) {
    return jdbc.queryForObject(
        "SELECT COUNT(*) FROM billing_outbox WHERE registration_id = ? "
            + "AND routing_key = 'registration.changed'",
        Integer.class,
        registrationId);
  }

  private String latestRegistrationSnapshot(UUID registrationId) {
    return jdbc.queryForObject(
        "SELECT body FROM billing_outbox WHERE registration_id = ? "
            + "AND routing_key = 'registration.changed' ORDER BY created_at DESC LIMIT 1",
        String.class,
        registrationId);
  }

  private String status(UUID registrationId) {
    return jdbc.queryForObject(
        "SELECT status FROM registration WHERE id = ?", String.class, registrationId);
  }

  private String inboxDisposition(UUID eventId) {
    return jdbc.queryForObject(
        "SELECT disposition FROM billing_result_inbox WHERE event_id = ?", String.class, eventId);
  }

  private BillingResult billingResult(UUID registrationId, String status, UUID eventId) {
    String requestBody =
        jdbc.queryForObject(
            "SELECT body FROM billing_outbox WHERE registration_id = ? "
                + "AND routing_key = 'billing.requested'",
            String.class,
            registrationId);
    try {
      JsonNode request = new com.fasterxml.jackson.databind.ObjectMapper().readTree(requestBody);
      return new BillingResult(
          eventId,
          "billing.result",
          "1.0.0",
          registrationId,
          2,
          UUID.fromString(request.path("correlationId").asText()),
          OffsetDateTime.now(ZoneOffset.UTC),
          "billing-mock",
          new BillingResultData(
              registrationId,
              UUID.fromString(request.path("data").path("attemptId").asText()),
              "PAYMENT",
              registrationId,
              ACTIVITY_ID,
              ORGANIZATION_ID,
              status,
              2500,
              "USD"));
    } catch (com.fasterxml.jackson.core.JsonProcessingException exception) {
      throw new AssertionError(exception);
    }
  }

  private AdmissionSnapshot admission(UUID activityId, int capacity, long amountMinor) {
    OffsetDateTime startsAt = OffsetDateTime.now(ZoneOffset.UTC).plusDays(1);
    return new AdmissionSnapshot(
        UUID.nameUUIDFromBytes(("snapshot-" + activityId).getBytes()),
        1,
        capacity,
        ORGANIZATION_ID,
        amountMinor,
        "USD",
        startsAt,
        startsAt.plusHours(1));
  }
}
