package br.uece.eventhub.events;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.UUID;

record EventInput(
    @NotNull UUID organizationId,
    @NotBlank @Size(max = 200) String title,
    @NotNull @Size(max = 10000) String description,
    @NotNull Instant startsAt,
    @NotNull Instant endsAt) {}

record EventView(
    UUID id,
    UUID organizationId,
    String title,
    String description,
    Instant startsAt,
    Instant endsAt,
    String status,
    long version) {}

record MoneyInput(
    @NotNull @Min(0) Long amountMinor, @NotBlank @Pattern(regexp = "^[A-Z]{3}$") String currency) {}

record MoneyView(long amountMinor, String currency) {}

record ActivityInput(
    @NotBlank @Size(max = 200) String title,
    @NotNull Instant startsAt,
    @NotNull Instant endsAt,
    @NotBlank String location,
    @NotBlank String responsible,
    @NotNull @Min(1) Integer capacity,
    @NotNull @Valid MoneyInput price) {}

record ActivityView(
    UUID id,
    UUID eventId,
    UUID organizationId,
    String title,
    Instant startsAt,
    Instant endsAt,
    String location,
    String responsible,
    int capacity,
    MoneyView price,
    long version,
    boolean capacityFrozen) {}

record AdmissionSnapshotView(
    ActivityView activity, UUID snapshotId, long snapshotVersion, int frozenCapacity) {}

record CommandResult(int status, Object body) {}

record StoredCommand(String requestHash, Integer responseStatus, String responseBody) {}

record ActivityChangedEvent(
    UUID eventId,
    String eventType,
    String schemaVersion,
    UUID aggregateId,
    long aggregateVersion,
    UUID correlationId,
    OffsetDateTime occurredAt,
    String producer,
    ActivityChangedData data) {}

record ActivityChangedData(
    UUID activityId,
    UUID eventId,
    UUID organizationId,
    String status,
    Instant startsAt,
    Instant endsAt) {}
