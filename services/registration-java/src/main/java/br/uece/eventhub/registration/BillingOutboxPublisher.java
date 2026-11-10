package br.uece.eventhub.registration;

import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.AmqpException;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.connection.CorrelationData.Confirm;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

@Component
class BillingOutboxPublisher {
  private static final Logger LOGGER = LoggerFactory.getLogger(BillingOutboxPublisher.class);
  private static final String EXCHANGE = "eventhub.v1";
  private static final long MINIMUM_CLAIM_LEASE_SECONDS = 30;

  private final JdbcTemplate jdbc;
  private final RabbitTemplate rabbitTemplate;
  private final TransactionTemplate transaction;
  private final IntegrationSettings settings;
  private final boolean postgres;

  BillingOutboxPublisher(
      JdbcTemplate jdbc,
      RabbitTemplate rabbitTemplate,
      PlatformTransactionManager transactionManager,
      IntegrationSettings settings) {
    this.jdbc = jdbc;
    this.rabbitTemplate = rabbitTemplate;
    this.settings = settings;
    transaction = new TransactionTemplate(transactionManager);
    transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    postgres =
        Boolean.TRUE.equals(
            jdbc.execute(
                (org.springframework.jdbc.core.ConnectionCallback<Boolean>)
                    connection -> isPostgres(connection)));
  }

  @Scheduled(fixedDelay = 30_000)
  void publishScheduled() {
    publishPending();
  }

  int publishPending() {
    int published = 0;
    while (true) {
      OutboxRecord record = claimNext();
      if (record == null) {
        return published;
      }
      try {
        publish(record);
        markPublished(record);
        published++;
      } catch (PublishNotConfirmedException
          | AmqpException
          | ExecutionException
          | TimeoutException exception) {
        deferForRetry(record, exception);
        LOGGER.error(
            "Billing outbox publish failed event_id={} attempt={} error_type={}",
            record.eventId(),
            record.attempts(),
            exception.getClass().getSimpleName(),
            exception);
        return published;
      } catch (InterruptedException exception) {
        Thread.currentThread().interrupt();
        deferForRetry(record, exception);
        LOGGER.error(
            "Billing outbox publish interrupted event_id={} attempt={}",
            record.eventId(),
            record.attempts(),
            exception);
        return published;
      }
    }
  }

  private OutboxRecord claimNext() {
    return transaction.execute(
        status -> {
          OffsetDateTime now = databaseNow();
          String lock = postgres ? " FOR UPDATE SKIP LOCKED" : "";
          List<OutboxRecord> candidates =
              jdbc.query(
                  "SELECT event_id, routing_key, correlation_id, body, attempts "
                      + "FROM billing_outbox WHERE published_at IS NULL AND suppressed_at IS NULL "
                      + "AND available_at <= ? "
                      + "AND (claim_expires_at IS NULL OR claim_expires_at <= ?) "
                      + "ORDER BY created_at, event_id LIMIT 1"
                      + lock,
                  (row, rowNumber) ->
                      new OutboxRecord(
                          row.getObject("event_id", UUID.class),
                          row.getString("routing_key"),
                          row.getObject("correlation_id", UUID.class),
                          row.getString("body"),
                          row.getInt("attempts"),
                          null),
                  now,
                  now);
          if (candidates.isEmpty()) {
            return null;
          }

          OutboxRecord candidate = candidates.getFirst();
          UUID claimToken = UUID.randomUUID();
          int changed =
              jdbc.update(
                  "UPDATE billing_outbox SET claim_token = ?, claim_expires_at = ?, "
                      + "attempts = attempts + 1 WHERE event_id = ? AND published_at IS NULL",
                  claimToken,
                  now.plusSeconds(claimLeaseSeconds()),
                  candidate.eventId());
          if (changed != 1) {
            throw new ApiException(
                503, "OUTBOX_INTEGRITY_ERROR", "Billing outbox claim changed unexpectedly.");
          }
          return candidate.withClaim(claimToken);
        });
  }

  private void publish(OutboxRecord record)
      throws InterruptedException, ExecutionException, TimeoutException {
    MessageProperties properties = new MessageProperties();
    properties.setContentType(MessageProperties.CONTENT_TYPE_JSON);
    properties.setDeliveryMode(MessageDeliveryMode.PERSISTENT);
    properties.setMessageId(record.eventId().toString());
    properties.setCorrelationId(record.correlationId().toString());
    CorrelationData correlationData = new CorrelationData(record.eventId().toString());
    rabbitTemplate.send(
        EXCHANGE,
        record.routingKey(),
        new Message(record.body().getBytes(StandardCharsets.UTF_8), properties),
        correlationData);
    Confirm confirm =
        correlationData.getFuture().get(settings.httpTimeoutSeconds(), TimeUnit.SECONDS);
    if (!confirm.isAck()) {
      throw new PublishNotConfirmedException("RabbitMQ did not confirm Billing request.");
    }
    if (correlationData.getReturned() != null) {
      throw new PublishNotConfirmedException("Billing request was returned as unroutable.");
    }
  }

  private void markPublished(OutboxRecord record) {
    int changed =
        jdbc.update(
            "UPDATE billing_outbox SET published_at = ?, claim_token = NULL, "
                + "claim_expires_at = NULL, last_error = NULL "
                + "WHERE event_id = ? AND claim_token = ? AND published_at IS NULL",
            databaseNow(),
            record.eventId(),
            record.claimToken());
    if (changed != 1) {
      throw new ApiException(
          503, "OUTBOX_INTEGRITY_ERROR", "Confirmed Billing request could not be recorded.");
    }
  }

  private void deferForRetry(OutboxRecord record, Exception exception) {
    OffsetDateTime availableAt = databaseNow().plusSeconds(retryDelaySeconds(record.attempts()));
    int changed =
        jdbc.update(
            "UPDATE billing_outbox SET available_at = ?, claim_token = NULL, "
                + "claim_expires_at = NULL, last_error = ? "
                + "WHERE event_id = ? AND claim_token = ? AND published_at IS NULL",
            availableAt,
            exception.getClass().getSimpleName(),
            record.eventId(),
            record.claimToken());
    if (changed != 1) {
      throw new ApiException(
          503, "OUTBOX_INTEGRITY_ERROR", "Failed Billing request retry could not be recorded.");
    }
  }

  private long retryDelaySeconds(int previousAttempts) {
    long delay = settings.retryInitialSeconds();
    for (int attempt = 1;
        attempt < previousAttempts && delay < settings.retryMaxSeconds();
        attempt++) {
      delay = Math.min(delay * 2, settings.retryMaxSeconds());
    }
    return delay;
  }

  private long claimLeaseSeconds() {
    return Math.max(MINIMUM_CLAIM_LEASE_SECONDS, 2L * settings.httpTimeoutSeconds());
  }

  private OffsetDateTime databaseNow() {
    String query = postgres ? "SELECT clock_timestamp()" : "SELECT CURRENT_TIMESTAMP";
    return jdbc.queryForObject(query, OffsetDateTime.class).withOffsetSameInstant(ZoneOffset.UTC);
  }

  private static boolean isPostgres(Connection connection) throws SQLException {
    return connection.getMetaData().getDatabaseProductName().toLowerCase().contains("postgresql");
  }

  private static class PublishNotConfirmedException extends RuntimeException {
    PublishNotConfirmedException(String message) {
      super(message);
    }
  }

  private record OutboxRecord(
      UUID eventId,
      String routingKey,
      UUID correlationId,
      String body,
      int attempts,
      UUID claimToken) {
    OutboxRecord withClaim(UUID token) {
      return new OutboxRecord(eventId, routingKey, correlationId, body, attempts + 1, token);
    }
  }
}
