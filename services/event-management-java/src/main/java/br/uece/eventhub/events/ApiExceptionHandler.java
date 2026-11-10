package br.uece.eventhub.events;

import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@RestControllerAdvice
public class ApiExceptionHandler {
  private static final Logger LOGGER = LoggerFactory.getLogger(ApiExceptionHandler.class);

  @ExceptionHandler(ApiException.class)
  ResponseEntity<Map<String, String>> handleApiException(
      ApiException exception, HttpServletRequest request) {
    return response(exception.status(), exception.code(), exception.getMessage(), request);
  }

  @ExceptionHandler({
    MethodArgumentNotValidException.class,
    HttpMessageNotReadableException.class,
    MethodArgumentTypeMismatchException.class,
    MissingRequestHeaderException.class
  })
  ResponseEntity<Map<String, String>> handleInvalidRequest(
      Exception exception, HttpServletRequest request) {
    String message = "Request does not match the API contract.";
    if (exception instanceof MethodArgumentNotValidException validationException) {
      FieldError firstError = validationException.getBindingResult().getFieldError();
      if (firstError != null) {
        message = "Invalid field: " + firstError.getField() + ".";
      }
    }
    return response(400, "INVALID_REQUEST", message, request);
  }

  @ExceptionHandler(DataAccessException.class)
  ResponseEntity<Map<String, String>> handleStorageFailure(
      DataAccessException exception, HttpServletRequest request) {
    String correlationId = correlationId(request);
    LOGGER.error(
        "Event Management storage operation failed type={} correlation_id={}",
        exception.getClass().getSimpleName(),
        correlationId);
    return response(
        503,
        "STORAGE_UNAVAILABLE",
        "The storage operation could not be completed; retry the request.",
        request);
  }

  @ExceptionHandler(Exception.class)
  ResponseEntity<Map<String, String>> handleUnexpected(
      Exception exception, HttpServletRequest request) {
    String correlationId = correlationId(request);
    LOGGER.error(
        "Event Management request failed unexpectedly type={} correlation_id={}",
        exception.getClass().getSimpleName(),
        correlationId,
        exception);
    return response(500, "INTERNAL_ERROR", "An internal error occurred.", request);
  }

  private static ResponseEntity<Map<String, String>> response(
      int status, String code, String message, HttpServletRequest request) {
    String correlationId = correlationId(request);
    return ResponseEntity.status(HttpStatus.valueOf(status))
        .header(CorrelationIdFilter.HEADER, correlationId)
        .body(Map.of("code", code, "message", message, "correlationId", correlationId));
  }

  private static String correlationId(HttpServletRequest request) {
    Object value = request.getAttribute(CorrelationIdFilter.ATTRIBUTE);
    return value == null ? UUID.randomUUID().toString() : value.toString();
  }
}
