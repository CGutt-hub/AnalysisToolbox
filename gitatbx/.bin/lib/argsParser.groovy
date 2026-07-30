// ArgsParser.groovy (im selben Ordner wie io_interface.nf belassen)

class ArgsParser {
    // Zentrales, JVM-basiertes Shell-Escaping
    static String escapeArg(def arg) {
        return arg.toString().replace("'", "'\\''")
    }

    // Formatiert die Eingangsdateien (Collection oder Single-File) zu Shell-Argumenten
    static String formatInputArgs(def input) {
        if (input instanceof Collection) {
            return input.collect { f_item -> "'${escapeArg(f_item)}'" }.join(' ')
        }
        return "'${escapeArg(input)}'"
    }

    // Der klammer-sichere Token-Parser
    static Map parse(def extraParams) {
        def result_map = [args: [], isGroupLog: false, isTerminal: false]
        if (!extraParams || extraParams.toString().trim() == "") {
            return result_map
        }
        
        def paramStr = extraParams.toString().trim()
        def args = []
        def currentArg = new StringBuilder()
        def depth = 0
        def inQuote = false
        
        for (int i = 0; i < paramStr.length(); i++) {
            def c = paramStr.substring(i, i+1)
            if (c == '"' || c == "'") {
                inQuote = !inQuote
                currentArg.append(c)
            } else if (!inQuote) {
                if (c == '[' || c == '{') { depth++ }
                else if (c == ']' || c == '}') { depth-- }
                else if (c == ' ' && depth == 0) {
                    if (currentArg.length() > 0) {
                        args.add(currentArg.toString())
                        currentArg.setLength(0)
                    }
                } else { currentArg.append(c) }
            } else { currentArg.append(c) }
        }
        if (currentArg.length() > 0) {
            args.add(currentArg.toString())
        }
        
        // Nextflow-spezifische Steuerungs-Flags bereinigen
        result_map.isGroupLog = args.remove('group_log')
        result_map.isTerminal  = args.remove('terminal')
        
        // Verpackt die verbleibenden Argumente direkt fertig escaped als Shell-String!
        result_map.extraArgsStr = args.collect { a_item -> "'${escapeArg(a_item)}'" }.join(' ')
        result_map.args = args
        
        return result_map
    }
}