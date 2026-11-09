package br.uece.eventhub.registration;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;

class RegistrationPolicyTest {
  private final ApplicationContextRunner context =
      new ApplicationContextRunner().withUserConfiguration(PolicyConfiguration.class);

  @Configuration(proxyBeanMethods = false)
  @EnableConfigurationProperties(RegistrationPolicy.class)
  static class PolicyConfiguration {}

  @Test
  void rejectsMissingPolicy() {
    context.run(result -> assertThat(result).hasFailed());
  }

  @Test
  void validatesExplicitPolicyAndRejectsInvalidPercentage() {
    var configured =
        context.withPropertyValues(
            "policy.reservation-expiry-minutes=30",
            "policy.waitlist-enabled=true",
            "policy.waitlist-offer-expiry-minutes=30",
            "policy.cancellation-window-days=7",
            "policy.refund-percentage=90");
    configured.run(result -> assertThat(result).hasNotFailed());
    configured
        .withPropertyValues("policy.refund-percentage=101")
        .run(result -> assertThat(result).hasFailed());
  }
}
