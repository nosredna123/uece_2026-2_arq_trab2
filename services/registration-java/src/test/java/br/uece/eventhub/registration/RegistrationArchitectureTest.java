package br.uece.eventhub.registration;

import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses;

import com.tngtech.archunit.core.importer.ImportOption;
import com.tngtech.archunit.junit.AnalyzeClasses;
import com.tngtech.archunit.junit.ArchTest;
import com.tngtech.archunit.lang.ArchRule;

@AnalyzeClasses(packages = "br.uece.eventhub", importOptions = ImportOption.DoNotIncludeTests.class)
class RegistrationArchitectureTest {
  @ArchTest
  static final ArchRule registrationDoesNotDependOnOtherDomains =
      noClasses()
          .that()
          .resideInAPackage("br.uece.eventhub.registration..")
          .should()
          .dependOnClassesThat()
          .resideInAnyPackage(
              "br.uece.eventhub.events..",
              "br.uece.eventhub.attendance..",
              "br.uece.eventhub.reporting..",
              "br.uece.eventhub.identity..",
              "br.uece.eventhub.billing..",
              "br.uece.eventhub.notification..");
}
