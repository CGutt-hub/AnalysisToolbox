include { NATIVE_JOIN } from '../processes/NATIVE_JOIN.nf'

workflow join {
    take:
        incoming_channels
        file_pattern

    main:
        def prepared_ch
        if (incoming_channels instanceof List) {
            def normalized = incoming_channels.collect { ch ->
                ch.map { item -> 
                    if (item instanceof List || item.getClass().isArray()) {
                        return tuple(item[0].toString(), item[-1])
                    }
                    return item
                }
            }
            def mixed = normalized[0]
            (1..<normalized.size()).each { idx -> mixed = mixed.mix(normalized[idx]) }
            prepared_ch = mixed.groupTuple(by: 0)
        } else {
            prepared_ch = incoming_channels.map { item ->
                if (item instanceof List || item.getClass().isArray()) {
                    return tuple(item[0].toString(), [item[-1]])
                }
                return item
            }
        }

        NATIVE_JOIN( prepared_ch, file_pattern )

    emit:
        merged_matrix = NATIVE_JOIN.out.merged_matrix
}