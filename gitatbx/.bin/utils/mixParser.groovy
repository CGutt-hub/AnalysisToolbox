// =========================================================================
// utils/mixParser.groovy (Agnostisches Synchronisations- und Parsing-Modul)
// =========================================================================
class mixParser {

    // 💡 UNVERÄNDERT: Sichert die fehlerfreie Kompilierung von finalize_l1 und finalize_l2!
    static Object mixChannelList(List channelList) {
        if (!channelList) return null
        def mixed = channelList[0].map { tuple_item -> tuple_item }
        (1..<channelList.size()).each { idx ->
            mixed = mixed.mix(channelList[idx].map { tuple_item -> tuple_item })
        }
        return mixed
    }

    /**
     * 💡 THE DYNAMIC SIGNATURE FIX: Changed from 'Object' to 'def'
     * Natively accepts any raw ArrayList wrappers containing live dataflow objects.
     * Completely strips structural wrappers down to clean physical text path arrays.
     */
    static List extractCleanPaths(def incomingSignals) {
        def cleanFiles = []
        if (incomingSignals == null) return cleanFiles
        
        def trackingQueue = []
        if (incomingSignals instanceof Collection) {
            trackingQueue.addAll(incomingSignals)
        } else {
            trackingQueue.add(incomingSignals)
        }
        
        int currentIndex = 0
        while (currentIndex < trackingQueue.size()) {
            def element = trackingQueue.get(currentIndex)
            if (element instanceof Collection) {
                trackingQueue.addAll(element)
            } else if (element != null) {
                // Strips all un-emitted dataflow or broadcast collection wrappers
                def plainPath = element.toString().replaceAll(/[\[\]\s,\"\']/, "").trim()
                if (plainPath.endsWith('.parquet')) {
                    cleanFiles.add(plainPath)
                }
            }
            currentIndex++
        }
        return cleanFiles
    }

    // 💡 UNVERÄNDERT: Garantiert die alphabetische Sortierungs-Hierarchie auf der Festplatte
    static String resolveHierarchicalName(String participantId, String stepLabel, String fallbackFilename) {
        if (participantId && participantId != 'null' && participantId != 'unknown' && participantId.trim() != '') {
            return "${participantId}_${stepLabel}"
        }
        
        def plainName = fallbackFilename ? new File(fallbackFilename.toString()).name : ""
        if (!plainName) return "metadata_matrix"
        
        def tokens = plainName.replace('.parquet', '').tokenize('_')
        if (tokens.size() >= 2) {
            def matchedId = tokens.find { tokenObj -> tokenObj.startsWith('EV') } ?: 'group'
            def remaining = tokens.findAll { tokenObj -> tokenObj != matchedId && tokenObj != 'staged' && tokenObj != 'stg' && tokenObj != 'join' }
            return "${matchedId}_${remaining.join('_')}"
        }
        return plainName.replace('.parquet', '')
    }
}