allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

val newBuildDir: Directory =
    rootProject.layout.buildDirectory
        .dir("../../build")
        .get()
rootProject.layout.buildDirectory.value(newBuildDir)

subprojects {
    val newSubprojectBuildDir: Directory = newBuildDir.dir(project.name)
    project.layout.buildDirectory.value(newSubprojectBuildDir)
}
// Plugins such as onnxruntime pin an old compileSdk (33) while their AndroidX
// dependencies require 34+/36+.  Lift every Android library module to 36.
//
// Registered BEFORE the evaluationDependsOn(":app") block below: that block
// triggers evaluation of every subproject, so an afterEvaluate registered
// afterwards would find the project already evaluated and throw.
subprojects {
    afterEvaluate {
        val androidExt = extensions.findByName("android") ?: return@afterEvaluate
        if (androidExt is com.android.build.gradle.LibraryExtension) {
            if (androidExt.compileSdk == null || androidExt.compileSdk!! < 36) {
                androidExt.compileSdk = 36
            }
        }
    }
}

subprojects {
    project.evaluationDependsOn(":app")
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
