nextflow.enable.dsl=2

include { DATA_DISCOVERY_L2 as process_discovery_l2 } from '../processes/DATA_DISCOVERY_L2.nf'

workflow data_discovery_l2 {
    take:
        finalized_l1_ch

    main:
        // Encapsulate process execution & value channel conversion internally
        registry_ch = process_discovery_l2( finalized_l1_ch ).registry.first()

    emit:
        registry_ch
}