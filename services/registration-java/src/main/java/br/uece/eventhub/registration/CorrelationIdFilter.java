package br.uece.eventhub.registration;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
class CorrelationIdFilter extends OncePerRequestFilter {
  static final String ATTRIBUTE = CorrelationIdFilter.class.getName() + ".correlationId";
  private static final String HEADER = "X-Correlation-ID";

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    String supplied = request.getHeader(HEADER);
    String correlationId;
    try {
      correlationId =
          supplied == null ? UUID.randomUUID().toString() : UUID.fromString(supplied).toString();
    } catch (IllegalArgumentException exception) {
      correlationId = UUID.randomUUID().toString();
    }
    request.setAttribute(ATTRIBUTE, correlationId);
    response.setHeader(HEADER, correlationId);
    filterChain.doFilter(request, response);
  }
}
