package br.uece.eventhub.registration;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.test.context.ActiveProfiles;

@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {
      "spring.autoconfigure.exclude="
          + "org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration,"
          + "org.springframework.boot.autoconfigure.flyway.FlywayAutoConfiguration,"
          + "org.springframework.boot.autoconfigure.amqp.RabbitAutoConfiguration"
    })
@ActiveProfiles("test")
class RegistrationApplicationTest {
  @Autowired private TestRestTemplate client;

  @Test
  void exposesHealthWithoutFalseRegistrationSuccess() {
    var response = client.getForEntity("/actuator/health", String.class);
    assertThat(response.getStatusCode().value()).isEqualTo(200);
    assertThat(response.getBody()).contains("\"status\":\"UP\"");
    assertThat(client.getForEntity("/registrations", String.class).getStatusCode().value())
        .isEqualTo(404);
  }
}
