// workflows/join.nf
include { NATIVE_JOIN } from '../processes/NATIVE_JOIN.nf'

workflow join {
    take:
        input_channels
        level_tag
        join_tag
        key_column
        scale_tag

    main:
        // 1. Extract consistent participant ID prefix across all channels for grouping
        def keyed_channels = input_channels.collect { ch ->
            ch.map { item ->
                def file_obj = item instanceof List ? item[-1] : item
                def matcher  = (file_obj.name =~ /^([^_]+_[^_]+)/)
                def pid      = matcher ? matcher[0][1] : file_obj.name.replaceAll(/\..*$/, '')
                return tuple(pid, file_obj)
            }
        }

        // 2. Reduce/Join streams by participant ID
        joined_tuples = keyed_channels.inject { acc, ch -> 
            acc.join(ch, by: 0) 
        }

        // 3. Strip key so NATIVE_JOIN receives ONLY raw path collections
        files_only = joined_tuples.map { tuple_data -> tuple_data.tail() }

        // 4. Run process cleanly with explicit join key and scale mode ('discrete' or 'continuous')
        NATIVE_JOIN( files_only, join_tag, level_tag, key_column, scale_tag )

    emit:
        mixed_matrix = NATIVE_JOIN.out[0]
}