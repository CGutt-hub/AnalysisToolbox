package lib.base

import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException
import java.nio.file.StandardOpenOption
import java.nio.ByteBuffer
import java.util.concurrent.ConcurrentHashMap

class BaseFileSystemUtils {

    private static final ConcurrentHashMap<String, Object> FILE_LOCKS = new ConcurrentHashMap<>()

    static void appendLog(File logFile, Object message) {
        if (!logFile) return
        
        def textMessage = (message instanceof List) ? message.join(" ") : (message != null ? message.toString() : "null")

        try {
            File canonicalFile = logFile.getCanonicalFile()
            File parentDir = canonicalFile.getParentFile()

            if (parentDir != null && !parentDir.exists()) {
                parentDir.mkdirs()
            }

            def pathKey = canonicalFile.getCanonicalPath()
            Object fileSyncObject = FILE_LOCKS.computeIfAbsent(pathKey, { new Object() })

            synchronized(fileSyncObject) {
                def timestamp = new Date().format("yyyy-MM-dd HH:mm:ss.SSS")
                def formattedMessage = "[${timestamp}] ${textMessage}\n"
                byte[] bytes = formattedMessage.getBytes("UTF-8")

                File lockFile = new File("${pathKey}.lock")
                def lockPath = lockFile.toPath()
                
                FileChannel lockChannel = FileChannel.open(
                    lockPath,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.WRITE
                )

                FileLock lock = null
                int retries = 0
                int maxRetries = 100

                while (retries < maxRetries) {
                    try {
                        lock = lockChannel.tryLock()
                        if (lock != null) break
                    } catch (OverlappingFileLockException e) {
                        // Expected during heavy parallel race conditions; backoff retry
                    }
                    retries++
                    Thread.sleep(25)
                }

                def path = canonicalFile.toPath()
                FileChannel dataChannel = FileChannel.open(
                    path,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.WRITE,
                    StandardOpenOption.APPEND
                )

                try {
                    dataChannel.write(ByteBuffer.wrap(bytes))
                    dataChannel.force(false)
                } finally {
                    try {
                        dataChannel.close()
                    } catch (Throwable ignored) {}

                    if (lock != null && lock.isValid()) {
                        lock.release()
                    }
                    try {
                        lockChannel.close()
                    } catch (Throwable ignored) {}
                }
            }

        } catch (Exception e) {
            System.err.println("[BaseFileSystemUtils] ERROR appending log to ${logFile?.path}: ${e.class.name} - ${e.message}")
        }
    }

    static void removeMarkers(File targetDir, String markerExtension) {
        if (!targetDir || !targetDir.exists()) return
        targetDir.eachFileRecurse { file ->
            if (file.isFile() && file.name.endsWith(markerExtension)) {
                file.delete()
            }
        }
    }
}