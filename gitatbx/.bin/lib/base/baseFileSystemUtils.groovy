package lib.base

import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException
import java.nio.file.StandardOpenOption
import java.nio.ByteBuffer
import java.util.concurrent.ConcurrentHashMap

class BaseFileSystemUtils {

    // Map of canonical file path strings to lock objects for strict intra-JVM thread synchronization
    private static final ConcurrentHashMap<String, Object> FILE_LOCKS = new ConcurrentHashMap<>()

    /**
     * Thread-safe and process-safe log appender using OS file locking & explicit path synchronization.
     * Robustly handles relative paths containing '..' and null payloads.
     */
    static void appendLog(File logFile, Object message) {
        if (!logFile) return
        
        def textMessage = (message instanceof List) ? message.join(" ") : (message != null ? message.toString() : "null")

        try {
            // Convert relative path with '..' into absolute canonical target
            File canonicalFile = logFile.getCanonicalFile()
            File parentDir = canonicalFile.getParentFile()

            if (parentDir != null && !parentDir.exists()) {
                parentDir.mkdirs()
            }

            def pathKey = canonicalFile.getCanonicalPath()
            // Retrieve or create a synchronized lock handle per unique file path
            Object fileSyncObject = FILE_LOCKS.computeIfAbsent(pathKey, { new Object() })

            synchronized(fileSyncObject) {
                def timestamp = new Date().format("yyyy-MM-dd HH:mm:ss.SSS")
                def formattedMessage = "[${timestamp}] ${textMessage}\n"
                byte[] bytes = formattedMessage.getBytes("UTF-8")

                def path = canonicalFile.toPath()
                FileChannel channel = FileChannel.open(
                    path,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.WRITE,
                    StandardOpenOption.APPEND
                )

                FileLock lock = null
                int retries = 0
                int maxRetries = 50

                // Attempt to acquire OS file lock cleanly, retrying on transient overlap
                while (retries < maxRetries) {
                    try {
                        lock = channel.tryLock()
                        if (lock != null) break
                    } catch (OverlappingFileLockException e) {
                        // Expected if another thread just released or is racing; brief sleep retry
                    }
                    retries++
                    Thread.sleep(20)
                }

                try {
                    if (lock != null) {
                        channel.write(ByteBuffer.wrap(bytes))
                    } else {
                        // Fallback write if lock acquisition timed out (ensures log doesn't drop)
                        channel.write(ByteBuffer.wrap(bytes))
                    }
                } finally {
                    if (lock != null && lock.isValid()) {
                        lock.release()
                    }
                    channel.close()
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