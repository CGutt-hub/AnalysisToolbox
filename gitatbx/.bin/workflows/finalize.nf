nextflow.enable.dsl=2

include { FINALIZE } from '../processes/FINALIZE.nf'

workflow finalize {
    take:
        root_signals
        sync_trigger_signals
        level

    main:
        def lvlUpper = level.toString().trim().toUpperCase()

        // Normalize input channels from single instances or lists
        def rootList = (root_signals instanceof List) ? root_signals : [root_signals]
        def syncList = (sync_trigger_signals instanceof List) ? sync_trigger_signals : [sync_trigger_signals]

        ch_root = rootList.drop(1).inject(rootList[0]) { acc, ch -> acc.mix(ch) }
        ch_sync = syncList.drop(1).inject(syncList[0]) { acc, ch -> acc.mix(ch) }

        if (lvlUpper == "L1") {
            // L1: Key by participant ID (PID) and join matching signals per subject
            ch_root_norm = ch_root
                .map { item ->
                    def fp = (item instanceof List || item.getClass().isArray()) ? item[-1] : item
                    def fn = new java.io.File(fp.toString()).name
                    def prefix = params.project_name + "_"
                    def pid = (item instanceof List || item.getClass().isArray()) ? item[0].toString() : (fn.startsWith(prefix) ? prefix + fn.substring(prefix.length()).split("[_\\.-]")[0] : fn)
                    return tuple(pid, fp)
                }
                .groupTuple(by: 0)

            ch_sync_norm = ch_sync
                .map { item ->
                    def fp = (item instanceof List || item.getClass().isArray()) ? item[-1] : item
                    def fn = new java.io.File(fp.toString()).name
                    def prefix = params.project_name + "_"
                    def pid = (item instanceof List || item.getClass().isArray()) ? item[0].toString() : (fn.startsWith(prefix) ? prefix + fn.substring(prefix.length()).split("[_\\.-]")[0] : fn)
                    return tuple(pid, fp)
                }
                .groupTuple(by: 0)

            ch_root_norm
                .join(ch_sync_norm, by: 0)
                .multiMap { _pid, roots, syncs ->
                    roots: [roots].flatten()
                    syncs: [syncs].flatten()
                }
                .set { ch_final_inputs }

            FINALIZE( ch_final_inputs.roots, ch_final_inputs.syncs, "l1" )

        } else if (lvlUpper == "L2") {
            // L2: Collect all cohort signals globally across participants
            ch_root_collected = ch_root.map { item -> (item instanceof List || item.getClass().isArray()) ? item[-1] : item }.collect()
            ch_sync_collected = ch_sync.map { item -> (item instanceof List || item.getClass().isArray()) ? item[-1] : item }.collect()

            FINALIZE( ch_root_collected, ch_sync_collected, "l2" )
        } else {
            throw new IllegalArgumentException("[FINALIZE_WORKFLOW] Invalid level parameter: '${level}'. Must be 'l1' or 'l2'.")
        }

    emit:
        finalized_path = (lvlUpper == "L1") ? FINALIZE.out.finalized_path.collect() : FINALIZE.out.finalized_path
}