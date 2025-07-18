# %%
"""
RAMSEY WITH VIRTUAL Z ROTATIONS
The program consists in playing a Ramsey sequence (x90 - idle_time - x90 - measurement) for different idle times.
Instead of detuning the qubit gates, the frame of the second x90 pulse is rotated (de-phased) to mimic an accumulated
phase acquired for a given detuning after the idle time.
This method has the advantage of playing resonant gates.

From the results, one can fit the Ramsey oscillations and precisely measure the qubit resonance frequency and T2*.

Prerequisites:
    - Having found the resonance frequency of the resonator coupled to the qubit under study (resonator_spectroscopy).
    - Having calibrated qubit pi pulse (x180) by running qubit, spectroscopy, rabi_chevron, power_rabi and updated the state.
    - (optional) Having calibrated the readout (readout_frequency, amplitude, duration_optimization IQ_blobs) for better SNR.

Next steps before going to the next node:
    - Update the qubits frequency (f_01) in the state.
    - Save the current state by calling machine.save("quam")
"""
from qualibrate import QualibrationNode, NodeParameters
from typing import Optional, Literal
from qualang_tools.multi_user import qm_session


class Parameters(NodeParameters):
    qubits: Optional[str] = ["qubitC1"]
    num_averages: int = 100
    frequency_detuning_in_mhz: float = 1.0
    min_wait_time_in_ns: int = 16
    max_wait_time_in_ns: int = 20000
    wait_time_step_in_ns: int = 50
    flux_point_joint_or_independent_or_arbitraryPulse_or_arbitraryDC: Literal['joint', 'independent', 'arbitraryPulse', 'arbitraryDC'] = "joint" 
    simulate: bool = False
    timeout: int = 100
    use_state_discrimination: bool = True


node = QualibrationNode(
    name="05b_Ramsey_EF", parameters=Parameters()
)

simulate = False

from qm.qua import *
from qm import SimulationConfig
from qualang_tools.results import progress_counter, fetching_tool
from qualang_tools.plot import interrupt_on_close
from qualang_tools.loops import from_array, get_equivalent_log_array
from qualang_tools.units import unit
from iqcc_research.quam_config.components import Quam
from iqcc_research.quam_config.macros import qua_declaration, multiplexed_readout, node_save, readout_state, readout_state_gef

import matplotlib.pyplot as plt
import numpy as np

import matplotlib
from iqcc_research.quam_config.lib.plot_utils import QubitGrid, grid_iter
from iqcc_research.quam_config.lib.save_utils import fetch_results_as_xarray
from qualibration_libs.analysis.fitting import fit_oscillation_decay_exp, oscillation_decay_exp

# matplotlib.use("TKAgg")


###################################################
#  Load QuAM and open Communication with the QOP  #
###################################################
# Class containing tools to help handle units and conversions.
u = unit(coerce_to_integer=True)
# Instantiate the QuAM class from the state file
machine = Quam.load()
# Generate the OPX and Octave configurations
config = machine.generate_config()
octave_config = machine.get_octave_config()
# Open Communication with the QOP
qmm = machine.connect()

# Get the relevant QuAM components
if node.parameters.qubits is None or node.parameters.qubits == '':
    qubits = machine.active_qubits
else:
    qubits = [machine.qubits[q] for q in node.parameters.qubits]
num_qubits = len(qubits)
# %%
###################
# The QUA program #
###################
n_avg = node.parameters.num_averages  # The number of averages

# Dephasing time sweep (in clock cycles = 4ns) - minimum is 4 clock cycles
idle_times = np.arange(
    node.parameters.min_wait_time_in_ns // 4,
    node.parameters.max_wait_time_in_ns // 4,
    node.parameters.wait_time_step_in_ns // 4,
)

# Detuning converted into virtual Z-rotations to observe Ramsey oscillation and get the qubit frequency
detuning = int(1e6 * node.parameters.frequency_detuning_in_mhz)
flux_point = node.parameters.flux_point_joint_or_independent_or_arbitraryPulse_or_arbitraryDC  # 'independent' or 'joint'
if flux_point == "arbitraryDC" or "arbitraryPulse":
    # arb_detunings = {q.name : q.xy.intermediate_frequency - q.arbitrary_intermediate_frequency for q in qubits}
    arb_detunings = {q.name: 0.0 for q in qubits}
    arb_flux_bias_offset = {q.name: q.z.arbitrary_offset for q in qubits}
else:
    arb_flux_bias_offset = {q.name: 0.0 for q in qubits}
    arb_detunings = {q.name: 0.0 for q in qubits}
    
with program() as ramsey:#
    I, I_st, Q, Q_st, n, n_st = qua_declaration(num_qubits=num_qubits)
    t = declare(int)  # QUA variable for the idle time
    sign = declare(int)  # QUA variable to change the sign of the detuning
    phi = declare(fixed)  # QUA variable for dephasing the second pi/2 pulse (virtual Z-rotation)
    if node.parameters.use_state_discrimination:
        state = [declare(int) for _ in range(num_qubits)]
        state_st = [declare_stream() for _ in range(num_qubits)]
        
    for i, q in enumerate(qubits):

        # Bring the active qubits to the minimum frequency point
        if flux_point == "independent":
            machine.apply_all_flux_to_zero()
            q.z.to_independent_idle()
        elif flux_point == "joint" or "arbitraryPulse":
            machine.apply_all_flux_to_joint_idle()
        elif flux_point == "arbitraryDC":
            machine.apply_all_flux_to_joint_idle()
            q.z.set_dc_offset(q.z.arbitrary_offset+q.z.joint_offset)
            q.xy.update_frequency(q.arbitrary_intermediate_frequency)
        else:
            machine.apply_all_flux_to_zero()

        for qb in qubits:
            wait(1000, qb.z.name)
        
        align()

        with for_(n, 0, n < n_avg, n + 1):
            save(n, n_st)
            with for_(*from_array(t, idle_times)):
                with for_(*from_array(sign, [-1,1])):
                    # Rotate the frame of the second x90 gate to implement a virtual Z-rotation
                    # 4*tau because tau was in clock cycles and 1e-9 because tau is ns
                    # assign(phi, Cast.mul_fixed_by_int(arb_detunings[q.name] + detuning * 1e-9, 4 * t ))
                    # assign(phi, Cast.mul_fixed_by_int(phi, sign))
                    assign(phi, Util.cond((sign == 1),  Cast.mul_fixed_by_int(detuning * 1e-9, 4 * t), Cast.mul_fixed_by_int(-detuning * 1e-9, 4 * t)))

                    align()
                    # # Strict_timing ensures that the sequence will be played without gaps
                    # with strict_timing_():
                    q.xy.update_frequency(q.xy.intermediate_frequency)
                    q.wait(10)
                    q.xy.play("x180")
                    q.xy.update_frequency(q.xy.intermediate_frequency - q.anharmonicity)
                    q.wait(10)
                    q.xy.play("EF_x90")
                    q.xy.frame_rotation_2pi(phi)
                    q.xy.play("EF_x90", amplitude_scale=0)
                    q.align()

                    # if reading out at the detuned frequency, no need to z-pulse during wait
                    if flux_point != "arbitraryPulse":
                        wait(t)

                    q.align()
                    q.xy.play("EF_x90")
                    q.xy.update_frequency(q.xy.intermediate_frequency)
                    q.wait(10)
                    q.xy.play("x180",amplitude_scale=0)
                    q.xy.play("x180")
                    q.xy.update_frequency(q.xy.intermediate_frequency - q.anharmonicity)
                    q.wait(10)
                    q.xy.play("EF_x180")

                    # Align the elements to measure after playing the qubit pulse.
                    align()
                    # Measure the state of the resonators

                    # save data
                    if node.parameters.use_state_discrimination:
                        readout_state(q, state[i])
                        save(state[i], state_st[i])
                    else:
                        q.resonator.measure("readout", qua_vars=(I[i], Q[i]))
                        save(I[i], I_st[i])
                        save(Q[i], Q_st[i])

                    # Wait for the qubits to decay to the ground state
                    q.resonator.wait(machine.thermalization_time * u.ns)

                    # Reset the frame of the qubits in order not to accumulate rotations
                    reset_frame(q.xy.name)

        align()

    with stream_processing():
        n_st.save("n")
        for i in range(num_qubits):
            if node.parameters.use_state_discrimination:
                state_st[i].buffer(2).buffer(len(idle_times)).average().save(f"state{i + 1}")
            else:
                I_st[i].buffer(2).buffer(len(idle_times)).average().save(f"I{i + 1}")
                Q_st[i].buffer(2).buffer(len(idle_times)).average().save(f"Q{i + 1}")


# %% {Simulate_or_execute}
if node.parameters.simulate:
    # Simulates the QUA program for the specified duration
    simulation_config = SimulationConfig(duration=10_000)  # In clock cycles = 4ns
    job = qmm.simulate(config, ramsey, simulation_config)
    job.get_simulated_samples().con1.plot()
    node.results = {"figure": plt.gcf()}
    node.machine = machine
    node.save()

else:
    with qm_session(qmm, config, timeout=node.parameters.timeout) as qm:
        job = qm.execute(ramsey)

        results = fetching_tool(job, ["n"], mode="live")
        while results.is_processing():
            n = results.fetch_all()[0]
            progress_counter(n, n_avg, start_time=results.start_time)


# %%
handles = job.result_handles
ds = fetch_results_as_xarray(handles, qubits, {"sign" : [-1,1], "time": idle_times})
ds = ds.assign_coords({'time' : (['time'], 4*idle_times)})
ds.time.attrs['long_name'] = 'idle_time'
ds.time.attrs['units'] = 'nS'
node.results = {}
node.results['ds'] = ds

# %%

if not simulate:
    if node.parameters.use_state_discrimination:
        fit = fit_oscillation_decay_exp(ds.state, 'time')
    else:
        fit = fit_oscillation_decay_exp(ds.I, 'time')
    fit.attrs = {'long_name' : 'time', 'units' : 'usec'}
    fitted =  oscillation_decay_exp(ds.time,
                                                    fit.sel(
                                                        fit_vals="a"),
                                                    fit.sel(
                                                        fit_vals="f"),
                                                    fit.sel(
                                                        fit_vals="phi"),
                                                    fit.sel(
                                                        fit_vals="offset"),
                                                    fit.sel(fit_vals="decay"))

    frequency = fit.sel(fit_vals = 'f')
    frequency.attrs = {'long_name' : 'frequency', 'units' : 'MHz'}

    decay = fit.sel(fit_vals = 'decay')
    decay.attrs = {'long_name' : 'decay', 'units' : 'nSec'}

    frequency = frequency.where(frequency>0,drop = True)

    decay = fit.sel(fit_vals = 'decay')
    decay.attrs = {'long_name' : 'decay', 'units' : 'nSec'}

    decay_res = fit.sel(fit_vals = 'decay_decay')
    decay_res.attrs = {'long_name' : 'decay', 'units' : 'nSec'}

    tau = 1/fit.sel(fit_vals='decay')
    tau.attrs = {'long_name' : 'T2*', 'units' : 'uSec'}

    tau_error = tau * (np.sqrt(decay_res)/decay)
    tau_error.attrs = {'long_name' : 'T2* error', 'units' : 'uSec'}


within_detuning = (1e9*frequency < 2 * detuning).mean(dim = 'sign') == 1
positive_shift = frequency.sel(sign = 1) > frequency.sel(sign = -1)
freq_offset = within_detuning * (frequency* fit.sign).mean(dim = 'sign') + ~within_detuning * positive_shift * (frequency).mean(dim = 'sign') -~within_detuning * ~positive_shift * (frequency).mean(dim = 'sign')

# freq_offset = (frequency* fit.sign).mean(dim = 'sign')
decay = 1e-9*tau.mean(dim = 'sign')
decay_error = 1e-9*tau_error.mean(dim = 'sign')
fit_results = {q.name : {'freq_offset' : 1e9*freq_offset.loc[q.name].values, 'decay' : decay.loc[q.name].values, 'decay_error' : decay_error.loc[q.name].values} for q in qubits}
node.results['fit_results'] = fit_results
for q in qubits:
    print(f"Frequency offset for qubit {q.name} : {(fit_results[q.name]['freq_offset']/1e6):.6f} MHz ")
    print(f"T2* for qubit {q.name} : {1e6*fit_results[q.name]['decay']:.2f} us")

# %%
grid = QubitGrid(ds, [q.grid_location for q in qubits])
for ax, qubit in grid_iter(grid):
    if node.parameters.use_state_discrimination:
        (ds.sel(sign = 1).loc[qubit].state).plot(ax = ax, x = 'time',
                                             c = 'C0', marker = '.', ms = 5.0, ls = '', label = "$\Delta$ = +")
        (ds.sel(sign = -1).loc[qubit].state).plot(ax = ax, x = 'time',
                                             c = 'C1', marker = '.', ms = 5.0, ls = '', label = "$\Delta$ = -")
        ax.plot(ds.time, fitted.loc[qubit].sel(sign = 1), c = 'black', ls = '-', lw=1)
        ax.plot(ds.time, fitted.loc[qubit].sel(sign = -1), c = 'red', ls = '-', lw=1)
        ax.set_ylabel('State')
    else:
        (ds.sel(sign = 1).loc[qubit].I*1e3).plot(ax = ax, x = 'time',
                                             c = 'C0', marker = '.', ms = 5.0, ls = '', label = "$\Delta$ = +")
        (ds.sel(sign = -1).loc[qubit].I*1e3).plot(ax = ax, x = 'time',
                                             c = 'C1', marker = '.', ms = 5.0, ls = '', label = "$\Delta$ = -")
        ax.set_ylabel('Trans. amp. I [mV]')
        ax.plot(ds.time, 1e3*fitted.loc[qubit].sel(sign = 1), c = 'black', ls = '-', lw=1)
        ax.plot(ds.time, 1e3*fitted.loc[qubit].sel(sign = -1), c = 'red', ls = '-', lw=1)
        
    ax.set_xlabel('Idle time [nS]')
    ax.set_title(qubit['qubit'])
    ax.text(0.5, 0.9, f'T2* = {1e6*fit_results[q.name]["decay"]:.1f} + {1e6*fit_results[q.name]["decay_error"]:.1f} usec', transform=ax.transAxes, fontsize=10,
    verticalalignment='top',ha='center', bbox=dict(facecolor='white', alpha=0.5))
    # ax.legend()
grid.fig.suptitle('Ramsey : I vs. idle time')
plt.tight_layout()
plt.gcf().set_figwidth(15)
plt.show()
node.results['figure'] = grid.fig

# %%
with node.record_state_updates():
    for q in qubits:
        if flux_point == "arbitraryPulse" or flux_point == "arbitraryDC":
            q.arbitrary_intermediate_frequency = np.round(q.arbitrary_intermediate_frequency + (fit_results[q.name]['freq_offset'])) # changed - to +
        else:
            q.anharmonicity = q.anharmonicity + int(fit_results[q.name]['freq_offset'])

# %%
node.results['initial_parameters'] = node.parameters.model_dump()
node.machine = machine
node.save()


# %%


