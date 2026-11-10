package br.uece.eventhub.registration;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Validated
@RestController
class RegistrationController {
  private final RegistrationService service;
  private final IdentityClient identityClient;
  private final EventManagementClient eventManagementClient;

  RegistrationController(
      RegistrationService service,
      IdentityClient identityClient,
      EventManagementClient eventManagementClient) {
    this.service = service;
    this.identityClient = identityClient;
    this.eventManagementClient = eventManagementClient;
  }

  @PostMapping("/registrations")
  ResponseEntity<RegistrationView> create(
      @Valid @RequestBody RegistrationRequest input,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
      HttpServletRequest request) {
    requireIdempotencyKey(idempotencyKey);
    String correlationId = correlationId(request);
    Principal principal = participant(request, correlationId);
    Optional<CommandResult> replay =
        service.replayCreate(principal, idempotencyKey, input.activityId());
    if (replay.isPresent()) {
      return ResponseEntity.status(replay.get().status()).body(replay.get().body());
    }
    AdmissionSnapshot snapshot =
        eventManagementClient.activateOrValidateAdmission(input.activityId(), correlationId);
    CommandResult result =
        service.create(
            principal,
            idempotencyKey,
            input.activityId(),
            snapshot,
            UUID.fromString(correlationId));
    return ResponseEntity.status(result.status()).body(result.body());
  }

  @GetMapping("/registrations")
  List<RegistrationView> list(
      @RequestParam(defaultValue = "20") int limit,
      @RequestParam(defaultValue = "0") int offset,
      HttpServletRequest request) {
    Principal principal = participant(request, correlationId(request));
    return service.listOwn(principal.subjectId(), limit, offset);
  }

  @GetMapping("/registrations/{id}")
  RegistrationView get(@PathVariable UUID id, HttpServletRequest request) {
    Principal principal = participant(request, correlationId(request));
    return service.getOwn(principal.subjectId(), id);
  }

  @PostMapping("/registrations/{id}/cancel")
  ResponseEntity<RegistrationView> cancel(
      @PathVariable UUID id,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
      HttpServletRequest request) {
    requireIdempotencyKey(idempotencyKey);
    Principal principal = participant(request, correlationId(request));
    CommandResult result = service.cancel(principal, idempotencyKey, id);
    return ResponseEntity.status(result.status()).body(result.body());
  }

  @PostMapping("/registrations/{id}/accept-offer")
  ResponseEntity<RegistrationView> acceptOffer(
      @PathVariable UUID id,
      @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
      HttpServletRequest request) {
    requireIdempotencyKey(idempotencyKey);
    String correlationId = correlationId(request);
    Principal principal = participant(request, correlationId);
    Optional<CommandResult> replay = service.replayAcceptOffer(principal, idempotencyKey, id);
    if (replay.isPresent()) {
      return ResponseEntity.status(replay.get().status()).body(replay.get().body());
    }
    UUID activityId = service.activityIdForOwnOffer(principal.subjectId(), id);
    AdmissionSnapshot snapshot =
        eventManagementClient.activateOrValidateAdmission(activityId, correlationId);
    CommandResult result =
        service.acceptOffer(
            principal, idempotencyKey, id, snapshot, UUID.fromString(correlationId));
    return ResponseEntity.status(result.status()).body(result.body());
  }

  @GetMapping("/activities/{activityId}/availability")
  AvailabilityView availability(@PathVariable UUID activityId, HttpServletRequest request) {
    PublicActivity activity =
        eventManagementClient.getPublishedActivity(activityId, correlationId(request));
    return service.availability(activity);
  }

  private Principal participant(HttpServletRequest request, String correlationId) {
    String authorization = request.getHeader("Authorization");
    if (authorization == null
        || !authorization.startsWith("Bearer ")
        || authorization.substring("Bearer ".length()).isBlank()) {
      throw new ApiException(401, "UNAUTHENTICATED", "A valid bearer credential is required.");
    }
    Principal principal =
        identityClient.validate(authorization.substring("Bearer ".length()), correlationId);
    if (principal.role() != Principal.Role.PARTICIPANT) {
      throw new ApiException(403, "FORBIDDEN", "Only participants may manage registrations.");
    }
    return principal;
  }

  private static String correlationId(HttpServletRequest request) {
    return request.getAttribute(CorrelationIdFilter.ATTRIBUTE).toString();
  }

  private static void requireIdempotencyKey(String key) {
    if (key == null || key.isBlank() || key.length() > 128) {
      throw new ApiException(
          400, "INVALID_IDEMPOTENCY_KEY", "A 1-128 character Idempotency-Key is required.");
    }
  }

  record RegistrationRequest(@NotNull UUID activityId) {}
}
