FROM eclipse-temurin:17-jdk-jammy

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends maven \
    && rm -rf /var/lib/apt/lists/*

# No application code is copied in -- file_changes/test_files are bind-mounted
# into /workspace at runtime by tools/sandbox_tools.py. Projects with a Gradle
# wrapper (gradlew) use their own bundled Gradle version, downloaded at
# run time; projects without one need a pom.xml (Maven, installed above).
# A project on neither Gradle-wrapper nor Maven isn't supported by this image.
