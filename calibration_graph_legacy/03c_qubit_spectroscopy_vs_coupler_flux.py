"""
        QUBIT SPECTROSCOPY VERSUS FLUX
This sequence involves doing a qubit spectroscopy for several flux biases in order to exhibit the qubit frequency
versus flux response.

Prerequisites:
    - Identification of the resonator's resonance frequency when coupled to the qubit in question (referred to as "resonator_spectroscopy").
    - Calibration of the IQ mixer connected to the qubit drive line (whether it's an external mixer or an Octave port).
    - Identification of the approximate qubit frequency ("qubit_spectroscopy").

Before proceeding to the next node:
    - Update the qubit frequency, in the state.
    - Update the relevant flux points in the state.
    - Update the frequency vs flux quadratic term in the state.
    - Save the current state
"""


# %% {Imports}
from datetime import datetime, timezone, timedelta
from qualibrate import QualibrationNode, NodeParameters
from iqcc_research.quam_config.components import Quam
from iqcc_research.quam_config.macros import qua_declaration
from qualibration_libs.data.processing import convert_IQ_to_V
from iqcc_research.quam_config.lib.plot_utils import QubitGrid, grid_iter
from iqcc_research.quam_config.lib.save_utils import fetch_results_as_xarray, load_dataset, get_node_id, save_node
from qualibration_libs.analysis.feature_detection import peaks_dips
from qualang_tools.results import progress_counter, fetching_tool
from qualang_tools.loops import from_array
from qualang_tools.multi_user import qm_session
from qualang_tools.units import unit
from qm import SimulationConfig
from qm.qua import *
from typing import Literal, Optional, List
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import find_peaks


# %% {Node_parameters}
class Parameters(NodeParameters):

    qubits: Optional[List[str]] = None
    qubit_pair: str = "coupler_qA5_qB4"
    num_averages: int = 25
    operation: str = "saturation"
    operation_amplitude_factor: Optional[float] = 0.1
    operation_len_in_ns: Optional[int] = None
    frequency_span_in_mhz: float = 125
    frequency_step_in_mhz: float = 0.5
    min_flux_offset_in_v: float = -0.2
    max_flux_offset_in_v: float = 0.03
    num_flux_points: int = 101
    flux_point_joint_or_independent: Literal["joint", "independent"] = "joint"
    simulate: bool = False
    simulation_duration_ns: int = 2500
    timeout: int = 100
    load_data_id: Optional[int] = None


node = QualibrationNode(name="03c_Qubit_Spectroscopy_vs_Coupler_Flux", parameters=Parameters())
node_id = get_node_id()


if node.parameters.qubit_pair is None:
    raise ValueError("Please specify the qubit_pair name")

# %% {Initialize_QuAM_and_QOP}
# Class containing tools to help handling units and conversions.
u = unit(coerce_to_integer=True)
# Instantiate the QuAM class from the state file
machine = Quam.load()
# Generate the OPX and Octave configurations
config = machine.generate_config()
# Open Communication with the QOP
if node.parameters.load_data_id is None:
    qmm = machine.connect()

qubit_pair = machine.qubit_pairs[node.parameters.qubit_pair]

# Get the relevant QuAM components
if node.parameters.qubits is None or node.parameters.qubits == "":
    qubits = qubit_pair.qubit_control
else:
    qubits = [machine.qubits[q] for q in node.parameters.qubits]
num_qubits = len(qubits)



# %% {QUA_program}
n_avg = node.parameters.num_averages  # The number of averages
operation = node.parameters.operation  # The qubit operation to play
# Adjust the pulse duration and amplitude to drive the qubit into a mixed state - can be None
operation_len = node.parameters.operation_len_in_ns
if node.parameters.operation_amplitude_factor:
    # pre-factor to the value defined in the config - restricted to [-2; 2)
    operation_amp = node.parameters.operation_amplitude_factor
else:
    operation_amp = 1.0
# Qubit detuning sweep with respect to their resonance frequencies
span = node.parameters.frequency_span_in_mhz * u.MHz
step = node.parameters.frequency_step_in_mhz * u.MHz
dfs = np.arange(-span // 2, span // 2, step, dtype=np.int32)
# Flux bias sweep
dcs = np.linspace(
    node.parameters.min_flux_offset_in_v,
    node.parameters.max_flux_offset_in_v,
    node.parameters.num_flux_points,
)
flux_point = node.parameters.flux_point_joint_or_independent  # 'independent' or 'joint'

with program() as multi_qubit_spec_vs_flux:
    # Macro to declare I, Q, n and their respective streams for a given number of qubit (defined in macros.py)
    I, I_st, Q, Q_st, n, n_st = qua_declaration(num_qubits=num_qubits)
    df = declare(int)  # QUA variable for the qubit frequency
    dc = declare(fixed)  # QUA variable for the flux dc level

    for i, qubit in enumerate(qubits):
        # Bring the active qubits to the minimum frequency point
        machine.set_all_fluxes(flux_point=flux_point, target=qubit)

        with for_(n, 0, n < n_avg, n + 1):
            save(n, n_st)

            with for_(*from_array(df, dfs)):
                # Update the qubit frequency
                qubit.xy.update_frequency(df + qubit.xy.intermediate_frequency)
                with for_(*from_array(dc, dcs)):
                    # Flux sweeping for a qubit
                    duration = operation_len * u.ns if operation_len is not None else qubit.xy.operations[operation].length * u.ns
                    # Bring the qubit to the desired point during the saturation pulse
                    qubit_pair.coupler.set_dc_offset(dc)
                    qubit_pair.align()
                    # Apply saturation pulse to all qubits
                    # qubit.xy.wait(qubit.z.settle_time * u.ns)
                    qubit.xy.play(
                        operation,
                        amplitude_scale=operation_amp,
                        duration=duration,
                    )
                    qubit.align()
                    # QUA macro to read the state of the active resonators
                    qubit.resonator.measure("readout", qua_vars=(I[i], Q[i]))
                    # save data
                    save(I[i], I_st[i])
                    save(Q[i], Q_st[i])
                    # Wait for the qubits to decay to the ground state
                    qubit.resonator.wait(qubit.resonator.depletion_time * u.ns)

        align()

    with stream_processing():
        n_st.save("n")
        for i, qubit in enumerate(qubits):
            I_st[i].buffer(len(dcs)).buffer(len(dfs)).average().save(f"I{i + 1}")
            Q_st[i].buffer(len(dcs)).buffer(len(dfs)).average().save(f"Q{i + 1}")


# %% {Simulate_or_execute}
if node.parameters.simulate:
    # Simulates the QUA program for the specified duration
    simulation_config = SimulationConfig(duration=node.parameters.simulation_duration_ns * 4)  # In clock cycles = 4ns
    job = qmm.simulate(config, multi_qubit_spec_vs_flux, simulation_config)
    # Get the simulated samples and plot them for all controllers
    samples = job.get_simulated_samples()
    fig, ax = plt.subplots(nrows=len(samples.keys()), sharex=True)
    for i, con in enumerate(samples.keys()):
        plt.subplot(len(samples.keys()),1,i+1)
        samples[con].plot()
        plt.title(con)
    plt.tight_layout()
    # Save the figure
    node.results = {"figure": plt.gcf()}
    node.machine = machine
    node.save()

elif node.parameters.load_data_id is None:
    date_time = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M:%S")
    with qm_session(qmm, config, timeout=node.parameters.timeout) as qm:
        job = qm.execute(multi_qubit_spec_vs_flux)
        results = fetching_tool(job, ["n"], mode="live")
        while results.is_processing():
            # Fetch results
            n = results.fetch_all()[0]
            # Progress bar
            progress_counter(n, n_avg, start_time=results.start_time)

# %% {Data_fetching_and_dataset_creation}
if not node.parameters.simulate:
    if node.parameters.load_data_id is not None:
        node = node.load_from_id(node.parameters.load_data_id)
        ds = node.results["ds"]
    else:
        # Fetch the data from the OPX and convert it into a xarray with corresponding axes (from most inner to outer loop)
        ds = fetch_results_as_xarray(job.result_handles, qubits, {"flux": dcs, "freq": dfs})
        # Convert IQ data into volts
        ds = convert_IQ_to_V(ds, qubits)
        # Derive the amplitude IQ_abs = sqrt(I**2 + Q**2)
        ds = ds.assign({"IQ_abs": np.sqrt(ds["I"] ** 2 + ds["Q"] ** 2)})
        # Add the resonator RF frequency axis of each qubit to the dataset coordinates for plotting
        ds = ds.assign_coords(
            {
                "freq_full": (
                    ["qubit", "freq"],
                    np.array([dfs + q.xy.RF_frequency for q in qubits]),
                )
            }
        )
        ds.freq_full.attrs["long_name"] = "Frequency"
        ds.freq_full.attrs["units"] = "GHz"
    # Add the dataset to the node
    node.results = {"ds": ds}

    # %% {Data_analysis}
    # # Find the resonance dips for each flux point
    peaks = peaks_dips(ds.I, dim="freq", prominence_factor=3)
    # obtain the derivative of the peaks vs flux
    peaks_d = np.abs(peaks.position.differentiate("flux"))
    # identify avoided crossings flux points
    avoided_crossing = {}
    for q in qubits:
        q_name = q.name
        peaks_d_per_qubit = peaks_d.loc[{"qubit": q_name}]
        divs_loc_vals, _ = find_peaks(peaks_d_per_qubit.values, height=peaks_d_per_qubit.values.max() * 0.1, distance=peaks_d_per_qubit["flux"].shape[0]//2) 
        divs_loc_vals = [min(divs_loc_vals), max(divs_loc_vals)] # if there are more than two the the fathest ones
        avoided_crossing[q_name] = peaks_d_per_qubit["flux"].values[divs_loc_vals]
        
    # Save fitting results
    if node.parameters.load_data_id is None:
        fit_results = {}
        for q in qubits:
            avoided_crossing_val = avoided_crossing[q.name]
            fit_results[q.name] = {"avoided_crossing": avoided_crossing_val, "decouple_offset": np.mean(avoided_crossing_val)}
        
        fit_results["decouple_offset_mean"] = np.mean([fit_results[q.name]["decouple_offset"] for q in qubits])
        node.results["fit_results"] = fit_results

    # %% {Plotting}
    grid = QubitGrid(ds, [q.grid_location for q in qubits])

    ds = ds.assign_coords(freq_GHz=ds.freq_full / 1e9)
    for ax, qubit in grid_iter(grid):
        ds.loc[qubit].I.plot(ax=ax, x="flux", y="freq_GHz", robust=True, add_colorbar=False)
        
        avoided_crossing = fit_results[qubit["qubit"]]["avoided_crossing"]
        decouple_offset = fit_results[qubit["qubit"]]["decouple_offset"]
        min_vline = ds["freq_GHz"].values.min()
        max_vline = ds["freq_GHz"].values.max()
        
        for flux_val in avoided_crossing:
            ax.vlines(flux_val, min_vline, max_vline, color="red", linestyle="--")

        ax.vlines(decouple_offset, min_vline, max_vline, color="red", linestyle="-")
        
        ax.vlines(qubit_pair.coupler.decouple_offset, min_vline, max_vline, color="orange", linestyle="-", label = "current")
        
        ax.legend(fontsize=8)
        ax.set_ylabel("Freq (GHz)")
        ax.set_xlabel("Flux (V)")
        ax.set_title(f"{qubit["qubit"]} - {qubit_pair.coupler.name}")
    grid.fig.suptitle(f"Qubit spectroscopy vs coupler flux \n {date_time} GMT+3  #{node_id}")
    
    plt.tight_layout()
    plt.show()
    node.results["figure"] = grid.fig

    # %% {Update_state}
    if node.parameters.load_data_id is None:
        with node.record_state_updates():
            # take decouple offset as the average of the avoided crossing points
            qubit_pair.coupler.decouple_offset = fit_results["decouple_offset_mean"]
    # %% {Save_results}
    node.results["ds"] = ds
    node.outcomes = {q.name: "successful" for q in qubits}
    node.results["initial_parameters"] = node.parameters.model_dump()
    node.machine = machine
    save_node(node)

# %%
