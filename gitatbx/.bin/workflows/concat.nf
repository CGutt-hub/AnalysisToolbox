include { NATIVE_CONCAT } from '../processes/NATIVE_CONCAT.nf'

workflow concat {
    take:
        incoming_signals
        output_name

    main:
        // Barrier collection: wait for all emissions to complete before passing the list 
        // to prevent premature evaluation of partial outputs.
        def collected_files = incoming_signals
            .map { item -> 
                if (item instanceof List || item.getClass().isArray()) {
                    return item[-1]
                }
                return item
            }
            .collect()

        NATIVE_CONCAT( collected_files, output_name )

    emit:
        cohort_matrix = NATIVE_CONCAT.out.cohort_matrix
}