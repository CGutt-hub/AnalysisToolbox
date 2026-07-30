include { FINALIZE_L2 } from '../processes/FINALIZE_L2.nf'

workflow finalize_l2 {
    take:
        l2_signals

    main:
        def mixed_ch
        if (l2_signals instanceof List) {
            def normalized = l2_signals.collect { ch ->
                ch.map { item -> 
                    if (item instanceof List || item.getClass().isArray()) {
                        return item[-1]
                    }
                    return item
                }
            }
            def mixed = normalized[0]
            (1..<normalized.size()).each { idx -> mixed = mixed.mix(normalized[idx]) }
            mixed_ch = mixed.collect()
        } else {
            mixed_ch = l2_signals.map { item -> 
                if (item instanceof List || item.getClass().isArray()) {
                    return item[-1]
                }
                return item
            }.collect()
        }

        FINALIZE_L2( mixed_ch )

        // Dynamically parse gitSync from .bin/lib/ relative to .bin/workflows/
        def gitSyncClass = new GroovyClassLoader().parseClass(moduleDir.resolve('../lib/gitSync.groovy').toFile())

        // Runs when L2 cohort summary finishes
        FINALIZE_L2.out.subscribe {
            gitSyncClass.syncRepository(workflow, params, "L2 cohort summary finalized")
        }

    emit:
        summary = FINALIZE_L2.out
}