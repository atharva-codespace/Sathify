plugins {
    id("com.android.application")
    // Must come after com.android.application: it hooks the Android variants to
    // generate google-services resources for each one.
    id("com.google.gms.google-services")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.sathify.app"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        // flutter_local_notifications 18.x compiles against java.time (and other
        // JDK 8+ APIs) that do not exist on the API 24 devices this app still
        // supports. Core library desugaring back-ports them at build time, and
        // the plugin's AAR metadata *requires* it — without this the build fails
        // with "Dependency ':flutter_local_notifications' requires core library
        // desugaring to be enabled for :app" before any code is compiled.
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // Identifies the app on Play and is the key Firebase ties
        // google-services.json to. Changing it later means re-registering the
        // Android app in Firebase, so it was moved off the com.example.*
        // placeholder (which Play rejects outright) before that setup happened.
        applicationId = "com.sathify.app"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

dependencies {
    // Supplies the back-ported java.* implementations that
    // isCoreLibraryDesugaringEnabled above splices in. Version is floored by
    // flutter_local_notifications 18.x, which documents 2.1.4 as its minimum.
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
}

flutter {
    source = "../.."
}
