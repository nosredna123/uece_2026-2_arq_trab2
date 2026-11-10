package br.uece.eventhub.registration;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.Channel;
import java.io.IOException;
import java.util.Objects;
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
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.connection.CorrelationData.Confirm;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;

@Component
class BillingResultConsumer {
  private static final Logger LOGGER = LoggerFactory.getLogger(BillingResultConsumer.class);
  private static final String EXCHANGE = "eventhub.v1";
  private static final String QUARANTINE_ROUTING_KEY = "registration.v1.quarantine";

  private final ObjectMapper objectMapper;
  private final RegistrationService service;
  private final RabbitTemplate rabbitTemplate;
  private final IntegrationSettings settings;

  BillingResultConsumer(
      ObjectMapper objectMapper,
      RegistrationService service,
      RabbitTemplate rabbitTemplate,
      IntegrationSettings settings) {
    this.objectMapper = objectMapper;
    this.service = service;
    this.rabbitTemplate = rabbitTemplate;
    this.settings = settings;
  }

  @RabbitListener(queues = "registration.v1", ackMode = "MANUAL")
  void consume(Message message, Channel channel) throws IOException {
    long deliveryTag = message.getMessageProperties().getDeliveryTag();
    BillingResult result;
    try {
      result = objectMapper.readValue(message.getBody(), BillingResult.class);
    } catch (IOException exception) {
      quarantineAndAcknowledge(message, channel, deliveryTag, "INVALID_BILLING_RESULT");
      return;
    }
    if (result == null
        || result.eventId() == null
        || result.correlationId() == null
        || !Objects.equals(
            message.getMessageProperties().getMessageId(), result.eventId().toString())
        || !Objects.equals(
            message.getMessageProperties().getCorrelationId(), result.correlationId().toString())) {
      quarantineAndAcknowledge(message, channel, deliveryTag, "INVALID_BILLING_RESULT");
      return;
    }
    try {
      service.applyBillingResult(result);
    } catch (ApiException exception) {
      if (exception.status() < 500) {
        quarantineAndAcknowledge(message, channel, deliveryTag, exception.code());
        return;
      }
      channel.basicNack(deliveryTag, false, true);
      throw exception;
    }
    channel.basicAck(deliveryTag, false);
  }

  private void quarantineAndAcknowledge(
      Message source, Channel channel, long deliveryTag, String errorCode) throws IOException {
    try {
      publishQuarantine(source, errorCode);
    } catch (InterruptedException exception) {
      Thread.currentThread().interrupt();
      channel.basicNack(deliveryTag, false, true);
      throw new IOException("Quarantine publication was interrupted.", exception);
    } catch (ExecutionException | TimeoutException | AmqpException exception) {
      channel.basicNack(deliveryTag, false, true);
      throw new IOException("Quarantine publication was not confirmed.", exception);
    }
    LOGGER.warn(
        "Quarantined Billing result message_id={} error_code={}",
        source.getMessageProperties().getMessageId(),
        errorCode);
    channel.basicAck(deliveryTag, false);
  }

  private void publishQuarantine(Message source, String errorCode)
      throws InterruptedException, ExecutionException, TimeoutException {
    MessageProperties properties = new MessageProperties();
    properties.setContentType(source.getMessageProperties().getContentType());
    properties.setDeliveryMode(MessageDeliveryMode.PERSISTENT);
    properties.setMessageId(source.getMessageProperties().getMessageId());
    properties.setCorrelationId(source.getMessageProperties().getCorrelationId());
    properties.setHeader("x-error-code", errorCode);
    properties.setHeader("x-original-queue", "registration.v1");
    Message quarantined = new Message(source.getBody(), properties);
    String confirmId = UUID.randomUUID().toString();
    CorrelationData correlationData = new CorrelationData(confirmId);
    rabbitTemplate.send(EXCHANGE, QUARANTINE_ROUTING_KEY, quarantined, correlationData);
    Confirm confirm =
        correlationData.getFuture().get(settings.httpTimeoutSeconds(), TimeUnit.SECONDS);
    if (!confirm.isAck() || correlationData.getReturned() != null) {
      throw new AmqpException("Billing result quarantine publication was not routable.");
    }
  }
}
