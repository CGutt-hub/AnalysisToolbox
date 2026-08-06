include { NATIVE_JOIN } from '../processes/NATIVE_JOIN.nf'

workflow join {
    take:
        incoming_channels
        file_pattern

    main:
        def gcl = new GroovyClassLoader(Thread.currentThread().contextClassLoader)
        gcl.addClasspath(moduleDir.resolve('../').toFile().absolutePath)
        
        def ChannelUtils        = gcl.parseClass(moduleDir.resolve('../lib/ChannelUtils.groovy').toFile())
        def BaseIdentifierUtils = gcl.parseClass(moduleDir.resolve('../lib/base/BaseIdentifierUtils.groovy').toFile())

        def formatted_pattern = file_pattern.endsWith('_join') ? file_pattern : "${file_pattern}_join"

        def prepared_ch
        if (incoming_channels instanceof List) {
            def channelCount = incoming_channels.size()

            if (channelCount == 0) {
                throw new IllegalArgumentException("[join.nf] incoming_channels list cannot be empty for pattern: '${formatted_pattern}'")
            }

            def normalized = incoming_channels.collect { ch ->
                ch.map { item -> 
                    if (item == null) {
                        throw new IllegalStateException("[join.nf] Null payload received before groupTuple aggregation in pattern: '${formatted_pattern}'")
                    }
                    
                    // Handle scalar Path objects coming from ID-agnostic NATIVE_MODULE outputs
                    if (item instanceof java.nio.file.Path || item instanceof java.io.File) {
                        def fileName = item.name.toString()
                        def derivedId = BaseIdentifierUtils.respondsTo('extractId') ? 
                                        BaseIdentifierUtils.extractId(fileName) : 
                                        fileName.tokenize('.')[0]
                        return tuple(derivedId, item)
                    }
                    
                    // Handle pre-existing tuple structures if present
                    if (item instanceof List || item.getClass().isArray()) {
                        def filePath = item[-1]
                        def fileName = new java.io.File(filePath.toString()).name
                        def derivedId = item[0]?.toString() ?: (
                            BaseIdentifierUtils.respondsTo('extractId') ? 
                            BaseIdentifierUtils.extractId(fileName) : 
                            fileName.tokenize('.')[0]
                        )
                        return tuple(derivedId, filePath)
                    }
                    
                    return item
                }
            }
            prepared_ch = ChannelUtils.mixChannelList(normalized).groupTuple(by: 0, size: channelCount)
        } else {
            prepared_ch = incoming_channels.map { item ->
                if (item == null) {
                    throw new IllegalStateException("[join.nf] Null payload received in pattern: '${formatted_pattern}'")
                }
                
                if (item instanceof java.nio.file.Path || item instanceof java.io.File) {
                    def fileName = item.name.toString()
                    def derivedId = BaseIdentifierUtils.respondsTo('extractId') ? 
                                    BaseIdentifierUtils.extractId(fileName) : 
                                    fileName.tokenize('.')[0]
                    return tuple(derivedId, [item])
                }
                
                if (item instanceof List || item.getClass().isArray()) {
                    def filePath = item[-1]
                    def fileName = new java.io.File(filePath.toString()).name
                    def derivedId = item[0]?.toString() ?: (
                        BaseIdentifierUtils.respondsTo('extractId') ? 
                        BaseIdentifierUtils.extractId(fileName) : 
                        fileName.tokenize('.')[0]
                    )
                    return tuple(derivedId, [filePath])
                }
                
                return item
            }
        }

        NATIVE_JOIN( prepared_ch, formatted_pattern )

    emit:
        merged_matrix = NATIVE_JOIN.out.merged_matrix
}