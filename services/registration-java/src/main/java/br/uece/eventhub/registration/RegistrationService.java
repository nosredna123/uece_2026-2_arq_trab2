package br.uece.eventhub.registration;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Connection;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

@Service
class RegistrationService {
  private static final String ACTIVE_STATUSES =
      "'WAITLISTED', 'OFFERED', 'PENDING_PAYMENT', 'CONFIRMED'";
  private static final String REGISTRATION_SELECT =
      "SELECT r.*, (SELECT w.sequence FROM waitlist_entry w "
          + "WHERE w.registration_id = r.id) AS waitlist_sequence FROM registration r ";
  private static final RowMapper<RegistrationView> REGISTRATION_MAPPER =
      (row, rowNumber) ->
          new RegistrationView(
              row.getObject("id", UUID.class),
              row.getObject("activity_id", UUID.class),
              row.getObject("participant_id", UUID.class),
              row.getObject("organization_id", UUID.class),
              row.getString("status"),
              row.getString("refund_status"),
              row.getObject("created_at", OffsetDateTime.class),
              row.getLong("version"),
              row.getObject("expires_at", OffsetDateTime.class),
              row.getObject("offer_expires_at", OffsetDateTime.class),
              row.getObject("waitlist_sequence", Long.class),
              new MoneyView(row.getLong("amount_minor"), row.getString("currency").trim()));
  private static final RowMapper<RegistrationView> REGISTRATION_LOCK_MAPPER =
      (row, rowNumber) ->
          new RegistrationView(
              row.getObject("id", UUID.class),
              row.getObject("activity_id", UUID.class),
              row.getObject("participant_id", UUID.class),
              row.getObject("organization_id", UUID.class),
              row.getString("status"),
              row.getString("refund_status"),
              row.getObject("created_at", OffsetDateTime.class),
              row.getLong("version"),
              row.getObject("expires_at", OffsetDateTime.class),
              row.getObject("offer_expires_at", OffsetDateTime.class),
              null,
              new MoneyView(row.getLong("amount_minor"), row.getString("currency").trim()));

  private final JdbcTemplate jdbc;
  private final ObjectMapper objectMapper;
  private final TransactionTemplate transaction;
  private final RegistrationPolicy policy;
  private final CapacityPolicy capacityPolicy = new CapacityPolicy();
  private final WaitlistPolicy waitlistPolicy = new WaitlistPolicy();
  private final boolean postgres;

  RegistrationService(
      JdbcTemplate jdbc,
      ObjectMapper objectMapper,
      PlatformTransactionManager transactionManager,
      RegistrationPolicy policy) {
    this.jdbc = jdbc;
    this.objectMapper = objectMapper;
    this.policy = policy;
    transaction = new TransactionTemplate(transactionManager);
    transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    postgres =
        Boolean.TRUE.equals(
            jdbc.execute(
                (org.springframework.jdbc.core.ConnectionCallback<Boolean>)
                    connection -> isPostgres(connection)));
  }

  Optional<CommandResult> replayCreate(Principal principal, String key, UUID activityId) {
    try {
      return transaction.execute(
          status ->
              findReceipt(principal.subjectId(), "CREATE", key, hash("create:" + activityId)));
    } catch (DataAccessException exception) {
      throw new ApiException(503, "STORAGE_UNAVAILABLE", "Registration storage is unavailable.");
    }
  }

  CommandResult create(
      Principal principal, String key, UUID activityId, AdmissionSnapshot snapshot) {
    String requestHash = hash("create:" + activityId);
    try {
      return transaction.execute(
          status -> {
            Optional<CommandResult> existing =
                findReceipt(principal.subjectId(), "CREATE", key, requestHash);
            if (existing.isPresent()) {
              return existing.get();
            }

            createLedgerIfMissing(activityId, snapshot);
            Ledger ledger = lockLedger(activityId);
            validateSnapshot(ledger, snapshot);

            Optional<CommandResult> raced =
                findReceipt(principal.subjectId(), "CREATE", key, requestHash);
            if (raced.isPresent()) {
              return raced.get();
            }

            Integer active =
                jdbc.query(
                    "SELECT 1 FROM registration WHERE activity_id = ? AND participant_id = ? "
                        + "AND status IN ("
                        + ACTIVE_STATUSES
                        + ")",
                    resultSet -> resultSet.next() ? 1 : null,
                    activityId,
                    principal.subjectId());
            if (active != null) {
              throw new ApiException(
                  409, "ALREADY_REGISTERED", "An active registration already exists.");
            }

            int afterPromotion = promoteNext(activityId, ledger.capacity(), ledger.occupied());
            if (afterPromotion != ledger.occupied()) {
              ledger = ledger.withOccupied(afterPromotion);
            }
            if (ledger.occupied() < 0 || ledger.occupied() > ledger.capacity()) {
              throw new ApiException(
                  503, "LEDGER_INTEGRITY_ERROR", "Activity capacity ledger is inconsistent.");
            }
            boolean waitlisted = ledger.occupied() == ledger.capacity();
            if (waitlisted && !policy.waitlistEnabled()) {
              throw new CapacityFullException();
            }
            int occupied = waitlisted ? ledger.occupied() : allocate(ledger);
            OffsetDateTime now = databaseNow();
            boolean paid = !waitlisted && snapshot.amountMinor() > 0;
            Long waitlistSequence = waitlisted ? nextWaitlistSequence(activityId) : null;
            OffsetDateTime expiresAt =
                paid ? now.plusMinutes(policy.reservationExpiryMinutes()) : null;
            RegistrationView registration =
                new RegistrationView(
                    UUID.randomUUID(),
                    activityId,
                    principal.subjectId(),
                    snapshot.organizationId(),
                    waitlisted ? "WAITLISTED" : paid ? "PENDING_PAYMENT" : "CONFIRMED",
                    "NONE",
                    now,
                    1,
                    expiresAt,
                    null,
                    waitlistSequence,
                    new MoneyView(snapshot.amountMinor(), snapshot.currency()));

            jdbc.update(
                "INSERT INTO registration (id, activity_id, organization_id, participant_id, "
                    + "status, refund_status, created_at, expires_at, offer_expires_at, "
                    + "amount_minor, currency, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                registration.id(),
                registration.activityId(),
                registration.organizationId(),
                registration.participantId(),
                registration.status(),
                registration.refundStatus(),
                registration.createdAt(),
                registration.expiresAt(),
                registration.offerExpiresAt(),
                registration.price().amountMinor(),
                registration.price().currency(),
                registration.version());
            if (waitlisted) {
              jdbc.update(
                  "INSERT INTO waitlist_entry (registration_id, activity_id, sequence) "
                      + "VALUES (?, ?, ?)",
                  registration.id(),
                  activityId,
                  waitlistSequence);
            } else {
              updateOccupied(activityId, ledger.occupied(), occupied);
            }
            CommandResult result = new CommandResult(201, registration);
            saveReceipt(principal.subjectId(), "CREATE", key, requestHash, result);
            return result;
          });
    } catch (CapacityFullException exception) {
      throw new ApiException(409, "CAPACITY_FULL", exception.getMessage());
    } catch (DuplicateKeyException exception) {
      return replayAfterUniqueConflict(principal.subjectId(), "CREATE", key, requestHash);
    } catch (DataAccessException exception) {
      throw new ApiException(503, "STORAGE_UNAVAILABLE", "Registration storage is unavailable.");
    }
  }

  CommandResult cancel(Principal principal, String key, UUID registrationId) {
    String requestHash = hash("cancel:" + registrationId);
    try {
      return transaction.execute(
          status -> {
            Optional<CommandResult> existing =
                findReceipt(principal.subjectId(), "CANCEL", key, requestHash);
            if (existing.isPresent()) {
              return existing.get();
            }

            UUID activityId =
                jdbc.query(
                    "SELECT activity_id FROM registration WHERE id = ?",
                    resultSet ->
                        resultSet.next() ? resultSet.getObject("activity_id", UUID.class) : null,
                    registrationId);
            if (activityId == null) {
              throw notFound();
            }
            Ledger ledger = lockLedger(activityId);
            Optional<CommandResult> raced =
                findReceipt(principal.subjectId(), "CANCEL", key, requestHash);
            if (raced.isPresent()) {
              return raced.get();
            }

            RegistrationView current =
                jdbc
                    .query(
                        "SELECT * FROM registration WHERE id = ? FOR UPDATE",
                        REGISTRATION_LOCK_MAPPER,
                        registrationId)
                    .stream()
                    .findFirst()
                    .orElseThrow(RegistrationService::notFound);
            if (!current.participantId().equals(principal.subjectId())) {
              throw notFound();
            }

            if (current.status().equals("CANCELLED")
                || current.status().equals("REFUSED")
                || current.status().equals("EXPIRED")) {
              CommandResult result = new CommandResult(200, current);
              saveReceipt(principal.subjectId(), "CANCEL", key, requestHash, result);
              return result;
            }
            if (current.status().equals("CONFIRMED") && current.price().amountMinor() > 0) {
              throw new ApiException(
                  409,
                  "REFUND_WORKFLOW_REQUIRED",
                  "Paid confirmed registrations require the refund workflow.");
            }

            boolean occupiesSlot =
                current.status().equals("CONFIRMED")
                    || current.status().equals("PENDING_PAYMENT")
                    || current.status().equals("OFFERED");
            if (!occupiesSlot && !current.status().equals("WAITLISTED")) {
              throw new ApiException(
                  409, "INVALID_STATE", "This registration cannot be cancelled.");
            }
            int occupied = occupiesSlot ? release(ledger) : ledger.occupied();
            jdbc.update(
                "UPDATE registration SET status = 'CANCELLED', version = version + 1 WHERE id = ?",
                registrationId);
            if (occupiesSlot) {
              updateOccupied(activityId, ledger.occupied(), occupied);
              promoteNext(activityId, ledger.capacity(), occupied);
            }

            RegistrationView cancelled = findRegistration(registrationId);
            CommandResult result = new CommandResult(200, cancelled);
            saveReceipt(principal.subjectId(), "CANCEL", key, requestHash, result);
            return result;
          });
    } catch (DuplicateKeyException exception) {
      return replayAfterUniqueConflict(principal.subjectId(), "CANCEL", key, requestHash);
    } catch (DataAccessException exception) {
      throw new ApiException(503, "STORAGE_UNAVAILABLE", "Registration storage is unavailable.");
    }
  }

  Optional<CommandResult> replayAcceptOffer(Principal principal, String key, UUID registrationId) {
    try {
      return transaction.execute(
          status ->
              findReceipt(
                  principal.subjectId(),
                  "ACCEPT_OFFER",
                  key,
                  hash("accept-offer:" + registrationId)));
    } catch (DataAccessException exception) {
      throw storageUnavailable();
    }
  }

  UUID activityIdForOwnOffer(UUID participantId, UUID registrationId) {
    try {
      UUID activityId =
          jdbc.query(
              "SELECT activity_id FROM registration WHERE id = ? AND participant_id = ?",
              resultSet -> resultSet.next() ? resultSet.getObject("activity_id", UUID.class) : null,
              registrationId,
              participantId);
      if (activityId == null) {
        throw notFound();
      }
      return activityId;
    } catch (DataAccessException exception) {
      throw storageUnavailable();
    }
  }

  CommandResult acceptOffer(
      Principal principal, String key, UUID registrationId, AdmissionSnapshot snapshot) {
    String requestHash = hash("accept-offer:" + registrationId);
    OfferOutcome outcome;
    try {
      outcome =
          transaction.execute(
              status -> {
                Optional<CommandResult> existing =
                    findReceipt(principal.subjectId(), "ACCEPT_OFFER", key, requestHash);
                if (existing.isPresent()) {
                  return new OfferOutcome(existing.get(), null);
                }

                UUID activityId =
                    jdbc.query(
                        "SELECT activity_id FROM registration WHERE id = ?",
                        resultSet ->
                            resultSet.next()
                                ? resultSet.getObject("activity_id", UUID.class)
                                : null,
                        registrationId);
                if (activityId == null) {
                  throw notFound();
                }
                Ledger ledger = lockLedger(activityId);
                Optional<CommandResult> raced =
                    findReceipt(principal.subjectId(), "ACCEPT_OFFER", key, requestHash);
                if (raced.isPresent()) {
                  return new OfferOutcome(raced.get(), null);
                }

                RegistrationView current =
                    jdbc
                        .query(
                            "SELECT * FROM registration WHERE id = ? FOR UPDATE",
                            REGISTRATION_LOCK_MAPPER,
                            registrationId)
                        .stream()
                        .findFirst()
                        .orElseThrow(RegistrationService::notFound);
                if (!current.participantId().equals(principal.subjectId())) {
                  throw notFound();
                }
                if (!current.status().equals("OFFERED")) {
                  throw new ApiException(
                      409, "INVALID_STATE", "This registration has no active offer.");
                }
                validateSnapshot(ledger, snapshot);

                OffsetDateTime now = databaseNow();
                if (current.offerExpiresAt() == null) {
                  throw new ApiException(
                      503, "WAITLIST_INTEGRITY_ERROR", "The active offer has no expiry deadline.");
                }
                if (!now.isBefore(current.offerExpiresAt())) {
                  expireOfferLocked(current, ledger);
                  return new OfferOutcome(
                      null,
                      new ApiException(409, "OFFER_EXPIRED", "The waitlist offer has expired."));
                }

                boolean paid = snapshot.amountMinor() > 0;
                OffsetDateTime paymentExpiresAt =
                    paid ? now.plusMinutes(policy.reservationExpiryMinutes()) : null;
                int changed =
                    jdbc.update(
                        "UPDATE registration SET status = ?, amount_minor = ?, currency = ?, "
                            + "expires_at = ?, offer_expires_at = NULL, version = version + 1 "
                            + "WHERE id = ? AND status = 'OFFERED'",
                        paid ? "PENDING_PAYMENT" : "CONFIRMED",
                        snapshot.amountMinor(),
                        snapshot.currency(),
                        paymentExpiresAt,
                        registrationId);
                if (changed != 1) {
                  throw new ApiException(
                      503, "WAITLIST_INTEGRITY_ERROR", "The active offer changed unexpectedly.");
                }
                RegistrationView accepted = findRegistration(registrationId);
                CommandResult result = new CommandResult(200, accepted);
                saveReceipt(principal.subjectId(), "ACCEPT_OFFER", key, requestHash, result);
                return new OfferOutcome(result, null);
              });
    } catch (DuplicateKeyException exception) {
      return replayAfterUniqueConflict(principal.subjectId(), "ACCEPT_OFFER", key, requestHash);
    } catch (DataAccessException exception) {
      throw storageUnavailable();
    }
    if (outcome.error() != null) {
      throw outcome.error();
    }
    return outcome.result();
  }

  void expireOffers() {
    try {
      OffsetDateTime now = databaseNow();
      List<UUID> expiredCandidates =
          jdbc.query(
              "SELECT id FROM registration WHERE status = 'OFFERED' "
                  + "AND offer_expires_at <= ? ORDER BY offer_expires_at, id",
              (row, rowNumber) -> row.getObject("id", UUID.class),
              now);
      expiredCandidates.forEach(this::expireOffer);
    } catch (DataAccessException exception) {
      throw storageUnavailable();
    }
  }

  private void expireOffer(UUID registrationId) {
    try {
      transaction.execute(
          status -> {
            UUID activityId =
                jdbc.query(
                    "SELECT activity_id FROM registration WHERE id = ?",
                    resultSet ->
                        resultSet.next() ? resultSet.getObject("activity_id", UUID.class) : null,
                    registrationId);
            if (activityId == null) {
              return null;
            }
            Ledger ledger = lockLedger(activityId);
            RegistrationView current =
                jdbc
                    .query(
                        "SELECT * FROM registration WHERE id = ? FOR UPDATE",
                        REGISTRATION_LOCK_MAPPER,
                        registrationId)
                    .stream()
                    .findFirst()
                    .orElse(null);
            if (current == null || !current.status().equals("OFFERED")) {
              return null;
            }
            if (current.offerExpiresAt() == null) {
              throw new ApiException(
                  503, "WAITLIST_INTEGRITY_ERROR", "The active offer has no expiry deadline.");
            }
            if (databaseNow().isBefore(current.offerExpiresAt())) {
              return null;
            }
            expireOfferLocked(current, ledger);
            return null;
          });
    } catch (DataAccessException exception) {
      throw storageUnavailable();
    }
  }

  private void expireOfferLocked(RegistrationView current, Ledger ledger) {
    int changed =
        jdbc.update(
            "UPDATE registration SET status = 'EXPIRED', version = version + 1 "
                + "WHERE id = ? AND status = 'OFFERED'",
            current.id());
    if (changed != 1) {
      throw new ApiException(
          503, "WAITLIST_INTEGRITY_ERROR", "The active offer changed unexpectedly.");
    }
    int released = release(ledger);
    updateOccupied(current.activityId(), ledger.occupied(), released);
    promoteNext(current.activityId(), ledger.capacity(), released);
  }

  RegistrationView getOwn(UUID participantId, UUID registrationId) {
    try {
      return jdbc
          .query(
              REGISTRATION_SELECT + "WHERE r.id = ? AND r.participant_id = ?",
              REGISTRATION_MAPPER,
              registrationId,
              participantId)
          .stream()
          .findFirst()
          .orElseThrow(RegistrationService::notFound);
    } catch (DataAccessException exception) {
      throw storageUnavailable();
    }
  }

  List<RegistrationView> listOwn(UUID participantId, int limit, int offset) {
    if (limit < 1 || limit > 100 || offset < 0) {
      throw new ApiException(400, "INVALID_PAGINATION", "Pagination parameters are invalid.");
    }
    try {
      return jdbc.query(
          REGISTRATION_SELECT
              + "WHERE r.participant_id = ? ORDER BY r.created_at, r.id LIMIT ? OFFSET ?",
          REGISTRATION_MAPPER,
          participantId,
          limit,
          offset);
    } catch (DataAccessException exception) {
      throw storageUnavailable();
    }
  }

  AvailabilityView availability(PublicActivity activity) {
    try {
      List<Ledger> ledgers =
          jdbc.query(
              "SELECT activity_id, snapshot_id, snapshot_version, frozen_capacity, occupied "
                  + "FROM activity_ledger WHERE activity_id = ?",
              (row, rowNumber) ->
                  new Ledger(
                      row.getObject("activity_id", UUID.class),
                      row.getObject("snapshot_id", UUID.class),
                      row.getLong("snapshot_version"),
                      row.getInt("frozen_capacity"),
                      row.getInt("occupied")),
              activity.id());
      int capacity = ledgers.isEmpty() ? activity.capacity() : ledgers.getFirst().capacity();
      int occupied = ledgers.isEmpty() ? 0 : ledgers.getFirst().occupied();
      if (occupied < 0 || occupied > capacity) {
        throw new ApiException(
            503, "LEDGER_INTEGRITY_ERROR", "Activity capacity ledger is inconsistent.");
      }
      return new AvailabilityView(
          activity.id(), capacity, occupied, capacity - occupied, databaseNow());
    } catch (DataAccessException exception) {
      throw storageUnavailable();
    }
  }

  private void createLedgerIfMissing(UUID activityId, AdmissionSnapshot snapshot) {
    if (postgres) {
      jdbc.update(
          "INSERT INTO activity_ledger (activity_id, snapshot_id, snapshot_version, frozen_capacity, "
              + "occupied) VALUES (?, ?, ?, ?, 0) ON CONFLICT (activity_id) DO NOTHING",
          activityId,
          snapshot.snapshotId(),
          snapshot.snapshotVersion(),
          snapshot.frozenCapacity());
      return;
    }
    try {
      jdbc.update(
          "INSERT INTO activity_ledger (activity_id, snapshot_id, snapshot_version, frozen_capacity, "
              + "occupied) VALUES (?, ?, ?, ?, 0)",
          activityId,
          snapshot.snapshotId(),
          snapshot.snapshotVersion(),
          snapshot.frozenCapacity());
    } catch (DuplicateKeyException ignored) {
      // Another transaction initialized this activity's ledger.
    }
  }

  private long nextWaitlistSequence(UUID activityId) {
    Long highest =
        jdbc.queryForObject(
            "SELECT COALESCE(MAX(sequence), 0) FROM waitlist_entry WHERE activity_id = ?",
            Long.class,
            activityId);
    try {
      return waitlistPolicy.nextSequence(highest == null ? 0 : highest);
    } catch (IllegalStateException exception) {
      throw new ApiException(503, "WAITLIST_INTEGRITY_ERROR", "Waitlist sequence is inconsistent.");
    }
  }

  private int promoteNext(UUID activityId, int capacity, int occupied) {
    if (occupied >= capacity) {
      return occupied;
    }
    List<UUID> candidates =
        jdbc.query(
            "SELECT r.id FROM waitlist_entry w JOIN registration r "
                + "ON r.id = w.registration_id WHERE w.activity_id = ? AND r.status = 'WAITLISTED' "
                + "ORDER BY w.sequence LIMIT 1",
            (row, rowNumber) -> row.getObject("id", UUID.class),
            activityId);
    if (candidates.isEmpty()) {
      return occupied;
    }
    int promotedOccupancy = allocate(capacity, occupied);
    UUID registrationId = candidates.getFirst();
    OffsetDateTime offerExpiresAt = databaseNow().plusMinutes(policy.waitlistOfferExpiryMinutes());
    int changed =
        jdbc.update(
            "UPDATE registration SET status = 'OFFERED', offer_expires_at = ?, "
                + "version = version + 1 WHERE id = ? AND status = 'WAITLISTED'",
            offerExpiresAt,
            registrationId);
    if (changed != 1) {
      throw new ApiException(
          503, "WAITLIST_INTEGRITY_ERROR", "The next waitlist entry changed unexpectedly.");
    }
    updateOccupied(activityId, occupied, promotedOccupancy);
    return promotedOccupancy;
  }

  private int allocate(int capacity, int occupied) {
    try {
      return capacityPolicy.allocate(capacity, occupied);
    } catch (IllegalStateException exception) {
      throw new ApiException(
          503, "LEDGER_INTEGRITY_ERROR", "Activity capacity ledger is inconsistent.");
    }
  }

  private RegistrationView findRegistration(UUID registrationId) {
    return jdbc
        .query(REGISTRATION_SELECT + "WHERE r.id = ?", REGISTRATION_MAPPER, registrationId)
        .stream()
        .findFirst()
        .orElseThrow(RegistrationService::notFound);
  }

  private Ledger lockLedger(UUID activityId) {
    return jdbc
        .query(
            "SELECT activity_id, snapshot_id, snapshot_version, frozen_capacity, occupied "
                + "FROM activity_ledger WHERE activity_id = ? FOR UPDATE",
            (row, rowNumber) ->
                new Ledger(
                    row.getObject("activity_id", UUID.class),
                    row.getObject("snapshot_id", UUID.class),
                    row.getLong("snapshot_version"),
                    row.getInt("frozen_capacity"),
                    row.getInt("occupied")),
            activityId)
        .stream()
        .findFirst()
        .orElseThrow(
            () ->
                new ApiException(
                    503, "LEDGER_MISSING", "Activity admission ledger could not be initialized."));
  }

  private void validateSnapshot(Ledger ledger, AdmissionSnapshot snapshot) {
    if (!ledger.snapshotId().equals(snapshot.snapshotId())
        || ledger.snapshotVersion() != snapshot.snapshotVersion()
        || ledger.capacity() != snapshot.frozenCapacity()) {
      throw new ApiException(
          409, "ADMISSION_SNAPSHOT_CONFLICT", "The activity admission snapshot has changed.");
    }
    if (snapshot.amountMinor() < 0
        || snapshot.currency() == null
        || !snapshot.currency().matches("[A-Z]{3}")) {
      throw new ApiException(
          503, "EVENT_MANAGEMENT_UNAVAILABLE", "Admission validation returned invalid pricing.");
    }
  }

  private void updateOccupied(UUID activityId, int oldValue, int newValue) {
    int changed =
        jdbc.update(
            "UPDATE activity_ledger SET occupied = ? WHERE activity_id = ? AND occupied = ?",
            newValue,
            activityId,
            oldValue);
    if (changed != 1) {
      throw new ApiException(
          503, "LEDGER_INTEGRITY_ERROR", "Activity capacity ledger changed unexpectedly.");
    }
  }

  private int allocate(Ledger ledger) {
    return allocate(ledger.capacity(), ledger.occupied());
  }

  private int release(Ledger ledger) {
    try {
      return capacityPolicy.release(ledger.capacity(), ledger.occupied());
    } catch (IllegalStateException exception) {
      throw new ApiException(
          503, "LEDGER_INTEGRITY_ERROR", "Activity capacity ledger is inconsistent.");
    }
  }

  private Optional<CommandResult> findReceipt(
      UUID principalId, String operation, String key, String requestHash) {
    List<Receipt> receipts =
        jdbc.query(
            "SELECT request_hash, response_status, response_body FROM command_receipt "
                + "WHERE principal_id = ? AND operation = ? AND idempotency_key = ?",
            (row, rowNumber) ->
                new Receipt(
                    row.getString("request_hash"),
                    row.getInt("response_status"),
                    row.getString("response_body")),
            principalId,
            operation,
            key);
    if (receipts.isEmpty()) {
      return Optional.empty();
    }
    Receipt receipt = receipts.getFirst();
    if (!receipt.requestHash().equals(requestHash)) {
      throw new ApiException(
          409, "IDEMPOTENCY_CONFLICT", "Idempotency key was reused with a different command.");
    }
    try {
      return Optional.of(
          new CommandResult(
              receipt.responseStatus(),
              objectMapper.readValue(receipt.responseBody(), RegistrationView.class)));
    } catch (JsonProcessingException exception) {
      throw new ApiException(
          503, "IDEMPOTENCY_RECEIPT_INVALID", "Stored command response could not be restored.");
    }
  }

  private void saveReceipt(
      UUID principalId, String operation, String key, String requestHash, CommandResult result) {
    try {
      jdbc.update(
          "INSERT INTO command_receipt (principal_id, operation, idempotency_key, request_hash, "
              + "response_status, response_body, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
          principalId,
          operation,
          key,
          requestHash,
          result.status(),
          objectMapper.writeValueAsString(result.body()),
          databaseNow());
    } catch (JsonProcessingException exception) {
      throw new ApiException(
          500, "RESPONSE_SERIALIZATION_FAILED", "Command response could not be persisted.");
    }
  }

  private CommandResult replayAfterUniqueConflict(
      UUID principalId, String operation, String key, String requestHash) {
    return transaction.execute(
        status ->
            findReceipt(principalId, operation, key, requestHash)
                .orElseThrow(
                    () ->
                        new ApiException(
                            409, "ALREADY_REGISTERED", "An active registration already exists.")));
  }

  private OffsetDateTime databaseNow() {
    String query = postgres ? "SELECT clock_timestamp()" : "SELECT CURRENT_TIMESTAMP";
    return jdbc.queryForObject(query, OffsetDateTime.class).withOffsetSameInstant(ZoneOffset.UTC);
  }

  private static boolean isPostgres(Connection connection) throws SQLException {
    return connection.getMetaData().getDatabaseProductName().toLowerCase().contains("postgresql");
  }

  private static String hash(String payload) {
    try {
      byte[] digest =
          MessageDigest.getInstance("SHA-256").digest(payload.getBytes(StandardCharsets.UTF_8));
      return HexFormat.of().formatHex(digest);
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 is required by the Java runtime.", exception);
    }
  }

  private static ApiException notFound() {
    return new ApiException(404, "NOT_FOUND", "Registration was not found.");
  }

  private static ApiException storageUnavailable() {
    return new ApiException(503, "STORAGE_UNAVAILABLE", "Registration storage is unavailable.");
  }

  private record OfferOutcome(CommandResult result, ApiException error) {}

  private record Ledger(
      UUID activityId, UUID snapshotId, long snapshotVersion, int capacity, int occupied) {
    Ledger withOccupied(int nextOccupied) {
      return new Ledger(activityId, snapshotId, snapshotVersion, capacity, nextOccupied);
    }
  }

  private record Receipt(String requestHash, int responseStatus, String responseBody) {}
}
