package br.uece.eventhub.registration;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.servlet.HandlerMapping;

@Component
class CorrelationIdFilter extends OncePerRequestFilter {
  private static final Logger LOGGER = LoggerFactory.getLogger(CorrelationIdFilter.class);
  static final String ATTRIBUTE = CorrelationIdFilter.class.getName() + ".correlationId";
  private static final String HEADER = "X-Correlation-ID";
  private final ObjectMapper objectMapper;

  CorrelationIdFilter(ObjectMapper objectMapper) {
    this.objectMapper = objectMapper;
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    String supplied = request.getHeader(HEADER);
    String correlationId;
    boolean invalid;
    try {
      correlationId =
          supplied == null ? UUID.randomUUID().toString() : UUID.fromString(supplied).toString();
      invalid = false;
    } catch (IllegalArgumentException exception) {
      correlationId = UUID.randomUUID().toString();
      invalid = true;
    }
    request.setAttribute(ATTRIBUTE, correlationId);
    response.setHeader(HEADER, correlationId);
    Map<String, String> previousContext = MDC.getCopyOfContextMap();
    MDC.put("correlationId", correlationId);
    MDC.put("httpMethod", request.getMethod());
    MDC.put("httpPath", request.getRequestURI());
    try {
      if (invalid) {
        response.setStatus(HttpServletResponse.SC_BAD_REQUEST);
        response.setContentType("application/json");
        objectMapper.writeValue(
            response.getOutputStream(),
            Map.of(
                "code",
                "INVALID_CORRELATION_ID",
                "message",
                "X-Correlation-ID must be a UUID.",
                "correlationId",
                correlationId));
        MDC.put("statusCode", Integer.toString(HttpServletResponse.SC_BAD_REQUEST));
        LOGGER.info("http.request.completed");
        return;
      }

      filterChain.doFilter(request, response);
      Object pathVariables = request.getAttribute(HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE);
      if (pathVariables instanceof Map<?, ?> variables && variables.get("id") != null) {
        MDC.put("registrationId", variables.get("id").toString());
      }
      MDC.put("statusCode", Integer.toString(response.getStatus()));
      LOGGER.info("http.request.completed");
    } finally {
      if (previousContext == null) {
        MDC.clear();
      } else {
        MDC.setContextMap(new HashMap<>(previousContext));
      }
    }
  }
}
