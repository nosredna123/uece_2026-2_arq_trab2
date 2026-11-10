package br.uece.eventhub.events;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Map;
import java.util.UUID;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class CorrelationIdFilter extends OncePerRequestFilter {
  public static final String HEADER = "X-Correlation-ID";
  public static final String ATTRIBUTE = CorrelationIdFilter.class.getName() + ".id";

  private final ObjectMapper objectMapper;

  public CorrelationIdFilter(ObjectMapper objectMapper) {
    this.objectMapper = objectMapper;
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    String suppliedId = request.getHeader(HEADER);
    String correlationId;
    try {
      correlationId =
          suppliedId == null
              ? UUID.randomUUID().toString()
              : UUID.fromString(suppliedId).toString();
    } catch (IllegalArgumentException exception) {
      correlationId = UUID.randomUUID().toString();
      response.setStatus(HttpServletResponse.SC_BAD_REQUEST);
      response.setContentType("application/json");
      response.setHeader(HEADER, correlationId);
      objectMapper.writeValue(
          response.getOutputStream(),
          Map.of(
              "code",
              "INVALID_CORRELATION_ID",
              "message",
              "X-Correlation-ID must be a UUID.",
              "correlationId",
              correlationId));
      return;
    }

    request.setAttribute(ATTRIBUTE, correlationId);
    response.setHeader(HEADER, correlationId);
    filterChain.doFilter(request, response);
  }
}
