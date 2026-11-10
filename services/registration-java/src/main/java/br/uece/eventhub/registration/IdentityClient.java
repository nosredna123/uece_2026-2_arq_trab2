package br.uece.eventhub.registration;

import java.time.Duration;
import java.util.Map;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

@Component
class IdentityClient {
  private final RestClient restClient;
  private final String serviceKey;

  IdentityClient(IntegrationSettings settings) {
    SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
    requestFactory.setConnectTimeout(Duration.ofSeconds(settings.httpTimeoutSeconds()));
    requestFactory.setReadTimeout(Duration.ofSeconds(settings.httpTimeoutSeconds()));
    restClient =
        RestClient.builder()
            .baseUrl(settings.identityBaseUrl())
            .requestFactory(requestFactory)
            .build();
    serviceKey = settings.internalServiceKey();
  }

  Principal validate(String token, String correlationId) {
    try {
      IdentityResponse response =
          restClient
              .post()
              .uri("/internal/identity/validate")
              .header("X-Service-Key", serviceKey)
              .header("X-Correlation-ID", correlationId)
              .body(Map.of("token", token))
              .retrieve()
              .body(IdentityResponse.class);
      if (response == null || response.subjectId() == null || response.role() == null) {
        throw new ApiException(
            503, "IDENTITY_UNAVAILABLE", "Identity validation returned an invalid principal.");
      }
      if ((response.role() == Principal.Role.ORGANIZER) != (response.organizationId() != null)) {
        throw new ApiException(
            503, "IDENTITY_UNAVAILABLE", "Identity validation returned an invalid principal.");
      }
      return new Principal(response.subjectId(), response.role(), response.organizationId());
    } catch (RestClientResponseException exception) {
      int status = exception.getStatusCode().value();
      if (status == 401) {
        throw new ApiException(401, "INVALID_IDENTITY", "The bearer credential is invalid.");
      }
      if (status == 403) {
        throw new ApiException(403, "FORBIDDEN", "The identity service denied this request.");
      }
      throw new ApiException(
          503, "IDENTITY_UNAVAILABLE", "Identity validation could not be completed.");
    } catch (RestClientException exception) {
      throw new ApiException(
          503, "IDENTITY_UNAVAILABLE", "Identity validation could not be completed.");
    }
  }

  private record IdentityResponse(
      java.util.UUID subjectId, Principal.Role role, java.util.UUID organizationId) {}
}
