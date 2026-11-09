FROM maven:3.9.9-eclipse-temurin-21 AS test
WORKDIR /workspace
COPY pom.xml .
COPY src src
RUN mvn -B -ff test
CMD ["mvn", "-B", "-ff", "test"]

FROM test AS build
RUN mvn -B -ff -DskipTests package

FROM eclipse-temurin:21.0.6_7-jre-jammy AS runtime
WORKDIR /app
COPY --from=build /workspace/target/event-management-0.1.0.jar app.jar
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
