package br.uece.eventhub.events;

import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import org.hibernate.validator.constraints.URL;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "integration")
public record IntegrationSettings(
    @NotBlank @URL String identityBaseUrl,
    @NotBlank String internalServiceKey,
    @NotBlank String registrationServiceKey,
    @NotNull @Positive Integer httpTimeoutSeconds,
    @NotNull @Positive Integer retryInitialSeconds,
    @NotNull @Positive Integer retryMaxSeconds) {

  @AssertTrue(message = "retry maximum must not be less than initial delay")
  public boolean isRetryOrderValid() {
    return retryInitialSeconds == null
        || retryMaxSeconds == null
        || retryInitialSeconds <= retryMaxSeconds;
  }

  @Override
  public String toString() {
    return "IntegrationSettings[credentials redacted]";
  }
}
