# OpenROAD's embedded OpenSTA or standalone OpenSTA. All values use ns/fF.
# No placement, wire RC estimation, or SPEF is part of this experiment.
set exit_on_error 1
set file_continue_on_error 0
if {$::env(P5_BACKEND) eq "openroad"} {
    read_lef $::env(P5_TECH_LEF)
    read_lef $::env(P5_CELL_LEF)
}
read_liberty $::env(P5_LIB)
read_verilog $::env(P5_NETLIST)
link_design $::env(P5_TOP)
source $::env(P5_SDC)
report_units
check_setup
if {[llength [all_clocks]] != 1} {error "Expected exactly one clock"}
set clk [lindex [all_clocks] 0]
if {[get_property $clk name] ne "core_clk"} {error "Unexpected clock name"}
if {abs([get_property $clk period] - $::env(P5_PERIOD)) > 1e-6} {
    error "Clock period does not match requested scenario"
}

set out $::env(P5_REPORT_DIR)
file mkdir $out
set stream [open $out/index.tsv w]
puts $stream "file_id\tendpoint_pin\tedge\tstatus\tarrival_ns\trequired_ns\tslack_ns"
set inventory [open $out/pins.tsv w]
puts $inventory "pin\tlib_pin\trole"

# all_registers -data_pins can include asynchronous RN/SN control pins.
# Query the actual pin objects and keep D only; do not resolve names as globs.
set data_pins [dict create]
foreach pin [all_registers -data_pins] {
    set name [get_property $pin full_name]
    set lib_pin [get_property $pin lib_pin_name]
    if {$lib_pin eq "D"} {
        puts $inventory "$name\t$lib_pin\tdata"
        dict set data_pins $name $pin
    } else {
        puts $inventory "$name\t$lib_pin\tcontrol_excluded"
    }
}
close $inventory
if {[dict size $data_pins] != $::env(P5_EXPECTED_FFS)} {
    error "STA and Yosys disagree on the number of actual register D pins"
}

set number 0
set present 0
set missing 0
foreach name [lsort [dict keys $data_pins]] {
    set pin [dict get $data_pins $name]
    foreach edge {rise fall} {
        set id [format "e%05d_%s" $number $edge]
        set paths [find_timing_paths -${edge}_to $pin -path_delay max \
            -group_path_count 1 -endpoint_path_count 1]
        if {[llength $paths] == 0} {
            # Distinguish an unreported/unreachable path from an unconstrained one.
            set loose [find_timing_paths -${edge}_to $pin -path_delay max \
                -unconstrained -group_path_count 1 -endpoint_path_count 1]
            set status no_timing_path
            if {[llength $loose] > 0} {set status unconstrained}
            puts $stream "$id\t$name\t$edge\t$status\t\t\t"
            incr missing
            continue
        }
        if {[llength $paths] != 1} {error "Ambiguous timing query at $name $edge"}
        set path [lindex $paths 0]
        set points [get_property $path points]
        set last [lindex $points end]
        set arrival [get_property $last arrival]
        set slack [get_property $path slack]
        set required [expr {$arrival + $slack}]
        puts $stream "$id\t$name\t$edge\tok\t$arrival\t$required\t$slack"
        if {$::env(P5_JSON) eq "1"} {
            # JSON quantities are SI, unlike get_property's command units.
            report_checks -${edge}_to $pin -path_delay max \
                -group_path_count 1 -endpoint_path_count 1 -format json > $out/$id.json
        }
        incr present
    }
    incr number
    if {$number % 200 == 0} {puts "P5 progress: $number register endpoints"}
}
close $stream
report_checks -path_delay max -group_path_count 10 -endpoint_path_count 1 \
    -fields {slew capacitance fanout} -format full_clock_expanded -digits 6 > $out/example_paths.txt
puts "P5_STA_OK endpoints=$number paths=$present missing=$missing period=$::env(P5_PERIOD)"
