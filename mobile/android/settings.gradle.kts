pluginManagement {
    val flutterSdkPath =
        run {
            val properties = java.util.Properties()
            file("local.properties").inputStream().use { properties.load(it) }
            val flutterSdkPath = properties.getProperty("flutter.sdk")
            require(flutterSdkPath != null) { "flutter.sdk not set in local.properties" }
            flutterSdkPath
        }

    includeBuild("$flutterSdkPath/packages/flutter_tools/gradle")

    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "9.0.1" apply false
    id("org.jetbrains.kotlin.android") version "2.3.20" apply false
    // Reads app/google-services.json and turns it into the Android string
    // resources firebase_core reads at startup. Without this plugin the JSON
    // file is inert: Firebase.initializeApp() finds no default options and
    // throws, which push_service.dart survives but with push switched off.
    //
    // Declared here rather than in the root build.gradle.kts that Firebase's
    // console suggests: the Flutter Android template has no plugins block in
    // the root file at all, and centralises plugin versions in this settings
    // plugins block instead (see com.android.application above). Same effect.
    id("com.google.gms.google-services") version "4.5.0" apply false
}

include(":app")
