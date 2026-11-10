package br.uece.eventhub.registration;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@RestControllerAdvice
class ApiExceptionHandler {
  @ExceptionHandler(ApiException.class)
  ResponseEntity<ErrorView> handleApiException(ApiException exception, HttpServletRequest request) {
    return ResponseEntity.status(exception.status())
        .body(new ErrorView(exception.code(), exception.getMessage(), correlationId(request)));
  }

  @ExceptionHandler({
    MethodArgumentNotValidException.class,
    HttpMessageNotReadableException.class,
    MethodArgumentTypeMismatchException.class
  })
  ResponseEntity<ErrorView> handleInvalidRequest(Exception exception, HttpServletRequest request) {
    return ResponseEntity.badRequest()
        .body(
            new ErrorView(
                "INVALID_REQUEST",
                "Request parameters or body are invalid.",
                correlationId(request)));
  }

  private static String correlationId(HttpServletRequest request) {
    Object value = request.getAttribute(CorrelationIdFilter.ATTRIBUTE);
    return value == null ? "" : value.toString();
  }
}
