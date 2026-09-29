# designs-v1; use after reading Nangate45 typical (ns/fF) and linking this top.
# Same scenario for the restricted BOG and full-library label branches.
create_clock -name core_clk -period 10.0 [get_ports {clk}]
set_clock_uncertainty 0.05 [get_clocks core_clk]
set_clock_transition 0.05 [get_clocks core_clk]
set_case_analysis 1 [get_ports {resetn}]
set p4_data_inputs {}
foreach p4_port [all_inputs] {
    if {[lsearch -exact {clk resetn} [get_property $p4_port name]] < 0} {
        lappend p4_data_inputs $p4_port
    }
}
set_input_delay -clock core_clk -max 1.0 $p4_data_inputs
set_input_delay -clock core_clk -min 0.0 $p4_data_inputs
set_input_transition 0.05 $p4_data_inputs
set_output_delay -clock core_clk -max 1.0 [all_outputs]
set_output_delay -clock core_clk -min 0.0 [all_outputs]
set_load 5.0 [all_outputs]
