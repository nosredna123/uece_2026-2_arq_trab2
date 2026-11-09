package br.uece.eventhub.registration;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "policy")
public record RegistrationPolicy(
    @NotNull @Positive Integer reservationExpiryMinutes,
    @NotNull Boolean waitlistEnabled,
    @NotNull @Positive Integer waitlistOfferExpiryMinutes,
    @NotNull @Positive Integer cancellationWindowDays,
    @NotNull @Min(1) @Max(100) Integer refundPercentage) {}
