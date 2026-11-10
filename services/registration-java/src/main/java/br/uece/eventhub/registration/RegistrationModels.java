package br.uece.eventhub.registration;

import java.time.OffsetDateTime;
import java.util.UUID;

record Principal(UUID subjectId, Role role, UUID organizationId) {
  enum Role {
    PARTICIPANT,
    ORGANIZER,
    GLOBAL_ADMIN
  }
}

record AdmissionSnapshot(
    UUID snapshotId,
    long snapshotVersion,
    int frozenCapacity,
    UUID organizationId,
    long amountMinor,
    String currency) {}

record PublicActivity(UUID id, int capacity) {}

record MoneyView(long amountMinor, String currency) {}

record RegistrationView(
    UUID id,
    UUID activityId,
    UUID participantId,
    UUID organizationId,
    String status,
    String refundStatus,
    OffsetDateTime createdAt,
    long version,
    OffsetDateTime expiresAt,
    OffsetDateTime offerExpiresAt,
    Long waitlistSequence,
    MoneyView price) {}

record AvailabilityView(
    UUID activityId, int capacity, int occupied, int available, OffsetDateTime asOf) {}

record CommandResult(int status, RegistrationView body) {}

record ErrorView(String code, String message, String correlationId) {}

record BillingRequest(
    UUID eventId,
    String eventType,
    String schemaVersion,
    UUID aggregateId,
    long aggregateVersion,
    UUID correlationId,
    OffsetDateTime occurredAt,
    String producer,
    BillingRequestData data) {}

record BillingRequestData(
    UUID operationId,
    UUID attemptId,
    String kind,
    UUID registrationId,
    UUID activityId,
    UUID organizationId,
    UUID participantId,
    long amountMinor,
    String currency,
    OffsetDateTime expiresAt) {}

record BillingResult(
    UUID eventId,
    String eventType,
    String schemaVersion,
    UUID aggregateId,
    long aggregateVersion,
    UUID correlationId,
    OffsetDateTime occurredAt,
    String producer,
    BillingResultData data) {}

record BillingResultData(
    UUID operationId,
    UUID attemptId,
    String kind,
    UUID registrationId,
    UUID activityId,
    UUID organizationId,
    String status,
    long amountMinor,
    String currency) {}
