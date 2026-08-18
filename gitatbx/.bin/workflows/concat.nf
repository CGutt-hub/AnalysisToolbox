// workflows/concat.nf
include { NATIVE_CONCAT } from '../processes/NATIVE_CONCAT.nf'

workflow concat {
    take:
        input_channels
        level_tag
        concat_tag

    main:
        // 1. Extract consistent identifier prefix across all channels for grouping
        def keyed_channels = input_channels.collect { ch ->
            ch.map { item ->
                def file_obj = item instanceof List ? item[-1] : item
                def matcher  = (file_obj.name =~ /^([^_]+_[^_]+)/)
                def pid      = matcher ? matcher[0][1] : file_obj.name.replaceAll(/\..*$/, '')
                return tuple(pid, file_obj)
            }
        }

        // 2. Reduce/Join streams by identifier key
        concatenated_tuples = keyed_channels.inject { acc, ch -> 
            acc.join(ch, by: 0) 
        }

        // 3. Strip key so NATIVE_CONCAT receives ONLY raw path collections
        files_only = concatenated_tuples.map { tuple_data -> tuple_data.tail() }

        // 4. Run process cleanly without tuple overhead
        NATIVE_CONCAT( files_only, concat_tag, level_tag )

    emit:
        mixed_matrix = NATIVE_CONCAT.out[0]
}