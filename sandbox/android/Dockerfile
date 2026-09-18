FROM eclipse-temurin:17-jdk-jammy

# NOTE (maintenance): the cmdline-tools build number and the installed
# platform/build-tools versions below go stale as Google ships new releases.
# If a target project's build.gradle asks for a newer compileSdk/build-tools
# than what's installed here, its build will fail with a clear "not found"
# error from sdkmanager/AGP -- bump the versions below and rebuild the image
# (see https://developer.android.com/studio#command-line-tools-only for the
# current cmdline-tools download).
ENV ANDROID_SDK_ROOT=/opt/android-sdk
ENV PATH="${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${ANDROID_SDK_ROOT}/platform-tools:${PATH}"
ENV ANDROID_CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends unzip curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p "${ANDROID_SDK_ROOT}/cmdline-tools" \
    && curl -sSL -o /tmp/cmdline-tools.zip "${ANDROID_CMDLINE_TOOLS_URL}" \
    && unzip -q /tmp/cmdline-tools.zip -d "${ANDROID_SDK_ROOT}/cmdline-tools" \
    && mv "${ANDROID_SDK_ROOT}/cmdline-tools/cmdline-tools" "${ANDROID_SDK_ROOT}/cmdline-tools/latest" \
    && rm /tmp/cmdline-tools.zip \
    && yes | sdkmanager --licenses > /dev/null \
    && sdkmanager --install "platform-tools" "platforms;android-34" "build-tools;34.0.0" > /dev/null

# No application code is copied in -- file_changes/test_files are bind-mounted
# into /workspace at runtime by tools/sandbox_tools.py. Only the Gradle
# wrapper's testDebugUnitTest (local JVM unit tests) is supported -- no
# emulator/system-image is installed here, so instrumented/androidTest tasks
# will fail; that requires hardware-accelerated virtualization this sandbox
# does not attempt to provide (see the multi-language design discussion for
# why that's out of scope, same as iOS Simulator tests).
