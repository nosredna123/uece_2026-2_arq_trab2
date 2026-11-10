package br.uece.eventhub.events;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class IdempotencyService {
  private static final int LOCK_STRIPES = 64;

  private final JdbcTemplate jdbcTemplate;
  private final ObjectMapper objectMapper;

  public IdempotencyService(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper) {
    this.jdbcTemplate = jdbcTemplate;
    this.objectMapper = objectMapper;
  }

  @Transactional
  public CommandResult execute(
      UUID actorId, String operation, String key, Object request, Command action) {
    if (key == null || key.isBlank() || key.length() > 128) {
      throw new ApiException(
          400, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must contain 1 to 128 characters.");
    }

    String requestHash = hash(request);
    int stripe = Math.floorMod(Objects.hash(actorId, operation, key), LOCK_STRIPES);
    jdbcTemplate.queryForObject(
        "SELECT stripe_id FROM command_lock WHERE stripe_id = ? FOR UPDATE", Integer.class, stripe);
    List<StoredCommand> storedCommands =
        jdbcTemplate.query(
            """
            SELECT request_hash, response_status, response_body
            FROM command_receipt
            WHERE actor_id = ? AND operation = ? AND idempotency_key = ?
            FOR UPDATE
            """,
            (rs, rowNum) ->
                new StoredCommand(
                    rs.getString("request_hash"),
                    (Integer) rs.getObject("response_status"),
                    rs.getString("response_body")),
            actorId,
            operation,
            key);
    if (!storedCommands.isEmpty()) {
      StoredCommand stored = storedCommands.get(0);
      if (!MessageDigest.isEqual(
          stored.requestHash().getBytes(StandardCharsets.US_ASCII),
          requestHash.getBytes(StandardCharsets.US_ASCII))) {
        throw new ApiException(
            409,
            "IDEMPOTENCY_CONFLICT",
            "The idempotency key was already used with another payload.");
      }
      if (stored.responseStatus() == null || stored.responseBody() == null) {
        throw new ApiException(
            503, "COMMAND_IN_PROGRESS", "The original command has not completed yet.");
      }
      try {
        return new CommandResult(
            stored.responseStatus(), objectMapper.readTree(stored.responseBody()));
      } catch (JsonProcessingException exception) {
        throw new ApiException(
            500, "INVALID_COMMAND_RECEIPT", "The stored command response is invalid.");
      }
    }

    jdbcTemplate.update(
        """
        INSERT INTO command_receipt (
            actor_id, operation, idempotency_key, request_hash
        ) VALUES (?, ?, ?, ?)
        """,
        actorId,
        operation,
        key,
        requestHash);
    CommandResult result = action.execute();
    try {
      String responseBody = objectMapper.writeValueAsString(result.body());
      jdbcTemplate.update(
          """
          UPDATE command_receipt
          SET response_status = ?, response_body = ?
          WHERE actor_id = ? AND operation = ? AND idempotency_key = ?
          """,
          result.status(),
          responseBody,
          actorId,
          operation,
          key);
    } catch (JsonProcessingException exception) {
      throw new ApiException(
          500,
          "COMMAND_RESPONSE_SERIALIZATION_FAILED",
          "The command response could not be stored.");
    }
    return result;
  }

  private String hash(Object request) {
    try {
      byte[] payload = objectMapper.writeValueAsBytes(request);
      return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(payload));
    } catch (JsonProcessingException | NoSuchAlgorithmException exception) {
      throw new ApiException(
          500, "COMMAND_HASH_FAILED", "The command request could not be fingerprinted.");
    }
  }

  @FunctionalInterface
  public interface Command {
    CommandResult execute();
  }
}
