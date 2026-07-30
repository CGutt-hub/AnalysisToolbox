import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption

class mapParser {
    static void exportCohortFiles(List<File> files, String targetFolder) {
        try {
            def targetDir = new File(targetFolder)
            if (!targetDir.exists()) targetDir.mkdirs()

            files.findAll { f -> f != null }.each { srcFile ->
                def destFile = new File(targetDir, srcFile.name)
                Files.copy(srcFile.toPath(), destFile.toPath(), StandardCopyOption.REPLACE_EXISTING)
            }
        } catch (Exception e) {
            println "[mapParser] ERROR beim Export nach ${targetFolder}: ${e.message}"
        }
    }
}
