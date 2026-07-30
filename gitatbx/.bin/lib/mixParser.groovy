class mixParser {

    static Object mixChannelList(List channelList) {
        if (!channelList) return null
        def mixed = channelList[0].map { tuple_item -> tuple_item }
        (1..<channelList.size()).each { idx ->
            mixed = mixed.mix(channelList[idx].map { tuple_item -> tuple_item })
        }
        return mixed
    }

    static List extractCleanPaths(def incomingSignals) {
        def cleanFiles = []
        if (incomingSignals == null) return cleanFiles
        
        def trackingQueue = []
        if (incomingSignals instanceof Collection) {
            trackingQueue.addAll(incomingSignals)
        } else if (incomingSignals != null && incomingSignals.getClass().isArray()) {
            trackingQueue.addAll(incomingSignals as List)
        } else {
            trackingQueue.add(incomingSignals)
        }
        
        int currentIndex = 0
        while (currentIndex < trackingQueue.size()) {
            def element = trackingQueue.get(currentIndex)
            if (element instanceof Collection) {
                trackingQueue.addAll(element)
            } else if (element != null && element.getClass().isArray()) {
                trackingQueue.addAll(element as List)
            } else if (element != null) {
                def plainPath = element.toString().replaceAll(/[\[\]\"\']/, "").trim()
                if (plainPath.endsWith('.parquet')) {
                    // Sanitize away duplicate extensions
                    def sanitized = plainPath.replaceAll(/(?i)(\.parquet)+$/, '') + '.parquet'
                    cleanFiles.add(sanitized)
                }
            }
            currentIndex++
        }
        return cleanFiles
    }

    static String resolveHierarchicalName(String participantId, String stepLabel, String fallbackFilename) {
        if (participantId && participantId != 'null' && participantId != 'unknown' && participantId.trim() != '') {
            def cleanStep = stepLabel ? stepLabel.replaceAll(/(?i)(\.parquet)+$/, '') : ""
            return cleanStep ? "${participantId}_${cleanStep}" : participantId
        }
        
        def plainName = fallbackFilename ? new File(fallbackFilename.toString()).name : ""
        if (!plainName) return "metadata_matrix"
        
        return plainName.replaceAll(/(?i)(\.parquet)+$/, '')
    }
}