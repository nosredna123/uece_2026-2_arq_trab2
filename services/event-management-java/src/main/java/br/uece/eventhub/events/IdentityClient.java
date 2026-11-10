package br.uece.eventhub.events;

import java.time.Duration;
import java.util.Map;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

@Component
public class IdentityClient {
  private final RestClient restClient;

  public IdentityClient(IntegrationSettings settings) {
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

  private final String serviceKey;

  public Principal validate(String token, String correlationId) {
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
      if (response == null) {
        throw new ApiException(
            503, "IDENTITY_UNAVAILABLE", "Identity validation returned no principal.");
      }
      return response.toPrincipal();
    } catch (RestClientResponseException exception) {
      HttpStatusCode status = exception.getStatusCode();
      if (status.value() == 401) {
        throw new ApiException(401, "INVALID_IDENTITY", "The bearer credential is invalid.");
      }
      if (status.value() == 403) {
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
      java.util.UUID subjectId, Principal.Role role, java.util.UUID organizationId) {
    Principal toPrincipal() {
      if (subjectId == null || role == null) {
        throw new ApiException(
            503, "IDENTITY_UNAVAILABLE", "Identity validation returned an invalid principal.");
      }
      if ((role == Principal.Role.ORGANIZER) != (organizationId != null)) {
        throw new ApiException(
            503, "IDENTITY_UNAVAILABLE", "Identity validation returned an invalid principal.");
      }
      return new Principal(subjectId, role, organizationId);
    }
  }
}
