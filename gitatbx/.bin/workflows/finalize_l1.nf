include { FINALIZE_L1 } from '../processes/FINALIZE_L1.nf'

workflow finalize_l1 {
    take:
        l1_signals
        participant_context

    main:
        def mixed_signals
        if (l1_signals instanceof List) {
            def normalized = l1_signals.collect { ch ->
                ch.map { item -> 
                    def pid = item[0].toString()
                    def filePath = item[-1]
                    return tuple(pid, filePath) 
                }
            }
            def mixed = normalized[0]
            (1..<normalized.size()).each { idx -> mixed = mixed.mix(normalized[idx]) }
            mixed_signals = mixed.groupTuple(by: 0)
        } else {
            mixed_signals = l1_signals.map { item -> 
                def pid = item[0].toString()
                def filePath = item[-1]
                return tuple(pid, [filePath]) 
            }
        }

        // Strict inner-join (remainder: false) prevents forwarding empty/failed channels down to FINALIZE_L1
        unified = participant_context
            .map { pid, folder -> tuple(pid.toString(), folder) }
            .join(mixed_signals, by: 0, remainder: false)
            .map { pid, folder, signals ->
                def cleanSignals = (signals instanceof List) ? signals.flatten() : [signals]
                return tuple(pid, folder, cleanSignals)
            }

        FINALIZE_L1( unified )

        // Dynamically parse gitSync from .bin/lib/ relative to .bin/workflows/
        def gitSyncClass = new GroovyClassLoader().parseClass(moduleDir.resolve('../lib/gitSync.groovy').toFile())

        // Sync git per finalized participant asset creation
        FINALIZE_L1.out.subscribe { item ->
            def participant_id = item[0]
            gitSyncClass.syncRepository(workflow, params, "L1 finalized for participant: ${participant_id}")
        }

    emit:
        finalized = FINALIZE_L1.out
}