package br.uece.eventhub.registration;

import java.time.Duration;
import java.util.UUID;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

@Component
class EventManagementClient {
  private final RestClient restClient;
  private final String registrationServiceKey;

  EventManagementClient(IntegrationSettings settings) {
    SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
    requestFactory.setConnectTimeout(Duration.ofSeconds(settings.httpTimeoutSeconds()));
    requestFactory.setReadTimeout(Duration.ofSeconds(settings.httpTimeoutSeconds()));
    restClient =
        RestClient.builder()
            .baseUrl(settings.eventManagementBaseUrl())
            .requestFactory(requestFactory)
            .build();
    registrationServiceKey = settings.registrationServiceKey();
  }

  AdmissionSnapshot activateOrValidateAdmission(UUID activityId, String correlationId) {
    try {
      AdmissionResponse response =
          restClient
              .post()
              .uri("/internal/activities/{activityId}/admission", activityId)
              .header("X-Service-Key", registrationServiceKey)
              .header("X-Correlation-ID", correlationId)
              .retrieve()
              .body(AdmissionResponse.class);
      if (response == null
          || response.activity() == null
          || response.activity().organizationId() == null
          || response.activity().price() == null
          || response.snapshotId() == null
          || response.snapshotVersion() < 1
          || response.frozenCapacity() < 1) {
        throw new ApiException(
            503,
            "EVENT_MANAGEMENT_UNAVAILABLE",
            "Admission validation returned an invalid snapshot.");
      }
      return new AdmissionSnapshot(
          response.snapshotId(),
          response.snapshotVersion(),
          response.frozenCapacity(),
          response.activity().organizationId(),
          response.activity().price().amountMinor(),
          response.activity().price().currency());
    } catch (RestClientResponseException exception) {
      throw dependencyException(exception, "Admission validation");
    } catch (RestClientException exception) {
      throw new ApiException(
          503, "EVENT_MANAGEMENT_UNAVAILABLE", "Admission validation could not be completed.");
    }
  }

  PublicActivity getPublishedActivity(UUID activityId, String correlationId) {
    try {
      ActivityResponse response =
          restClient
              .get()
              .uri("/activities/{activityId}", activityId)
              .header("X-Correlation-ID", correlationId)
              .retrieve()
              .body(ActivityResponse.class);
      if (response == null || response.id() == null || response.capacity() < 1) {
        throw new ApiException(
            503, "EVENT_MANAGEMENT_UNAVAILABLE", "Activity lookup returned an invalid response.");
      }
      return new PublicActivity(response.id(), response.capacity());
    } catch (RestClientResponseException exception) {
      throw dependencyException(exception, "Activity lookup");
    } catch (RestClientException exception) {
      throw new ApiException(503, "EVENT_MANAGEMENT_UNAVAILABLE", "Activity lookup failed.");
    }
  }

  private ApiException dependencyException(
      RestClientResponseException exception, String operation) {
    return switch (exception.getStatusCode().value()) {
      case 404 -> new ApiException(404, "NOT_FOUND", "The requested activity was not found.");
      case 422 ->
          new ApiException(
              422, "ACTIVITY_INELIGIBLE", "The activity is not eligible for admission.");
      default ->
          new ApiException(
              503, "EVENT_MANAGEMENT_UNAVAILABLE", operation + " could not be completed.");
    };
  }

  private record AdmissionResponse(
      ActivityResponse activity, UUID snapshotId, long snapshotVersion, int frozenCapacity) {}

  private record ActivityResponse(
      UUID id,
      UUID eventId,
      UUID organizationId,
      String title,
      java.time.Instant startsAt,
      java.time.Instant endsAt,
      String location,
      String responsible,
      int capacity,
      MoneyResponse price,
      long version,
      boolean capacityFrozen) {}

  private record MoneyResponse(long amountMinor, String currency) {}
}
