"""
        DRAG PULSE CALIBRATION (YALE METHOD)
The sequence consists in applying successively x180-y90 and y180-x90 to the qubit while varying the DRAG
coefficient alpha. The qubit is reset to the ground state between each sequence and its state is measured and stored.
Each sequence will bring the qubit to the same state only when the DRAG coefficient is set to its correct value.

This protocol is described in Reed's thesis (Fig. 5.8) https://rsl.yale.edu/sites/default/files/files/RSL_Theses/reed.pdf
This protocol was also cited in: https://doi.org/10.1103/PRXQuantum.2.040202

Prerequisites:
    - Having found the resonance frequency of the resonator coupled to the qubit under study (resonator_spectroscopy).
    - Having calibrated qubit pi pulse (x180) by running qubit spectroscopy, power_rabi, ramsey and updated the state.
    - (optional) Having calibrated the readout (readout_frequency, amplitude, duration_optimization IQ_blobs) for better SNR and state discrimination.
    - Set the DRAG coefficient to a non-zero value in the config: such as drag_coef = 1
    - Set the desired flux bias.

Next steps before going to the next node:
    - Update the DRAG coefficient (alpha) in the state.
"""


# %% {Imports}
from qualibrate import QualibrationNode, NodeParameters
from iqcc_research.quam_config.components import Quam
from iqcc_research.quam_config.macros import qua_declaration, active_reset
from iqcc_research.quam_config.lib.plot_utils import QubitGrid, grid_iter
from iqcc_research.quam_config.lib.save_utils import fetch_results_as_xarray, load_dataset, save_node
from iqcc_research.quam_config.trackable_object import tracked_updates
from qualang_tools.results import progress_counter, fetching_tool
from qualang_tools.loops import from_array
from qualang_tools.multi_user import qm_session
from qualang_tools.units import unit
from qm import SimulationConfig
from qm.qua import *
from typing import Literal, Optional, List
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr


# %% {Node_parameters}
class Parameters(NodeParameters):
    qubits: Optional[List[str]] = None
    num_averages: int = 1000
    operation: str = "x180"
    min_amp_factor: float = -2
    max_amp_factor: float = 2.0
    amp_factor_step: float = 0.05
    flux_point_joint_or_independent: Literal["joint", "independent"] = "joint"
    reset_type_thermal_or_active: Literal["thermal", "active"] = "active"
    simulate: bool = False
    simulation_duration_ns: int = 2500
    timeout: int = 100
    load_data_id: Optional[int] = None
    multiplexed: bool = True

node = QualibrationNode(name="09c_DRAG_Calibration_180_90", parameters=Parameters())


# %% {Initialize_QuAM_and_QOP}
# Class containing tools to help handling units and conversions.
u = unit(coerce_to_integer=True)
# Instantiate the QuAM class from the state file
machine = Quam.load()
# Generate the OPX and Octave configurations
if node.parameters.qubits is None or node.parameters.qubits == "":
    qubits = machine.active_qubits
else:
    qubits = [machine.qubits[q] for q in node.parameters.qubits]
num_qubits = len(qubits)

# Update the readout power to match the desired range, this change will be reverted at the end of the node.
tracked_qubits = []
for q in qubits:
    with tracked_updates(q, auto_revert=False, dont_assign_to_none=True) as q:
        q.xy.operations["x180"].alpha = -1.0
        tracked_qubits.append(q)

config = machine.generate_config()
# Open Communication with the QOP
if node.parameters.load_data_id is None:
    qmm = machine.connect()


# %% {QUA_program}
n_avg = node.parameters.num_averages  # The number of averages
flux_point = node.parameters.flux_point_joint_or_independent  # 'independent' or 'joint'
reset_type = node.parameters.reset_type_thermal_or_active  # "active" or "thermal"
operation = node.parameters.operation  # The qubit operation to play
# Pulse amplitude sweep (as a pre-factor of the qubit pulse amplitude) - must be within [-2; 2)
amps = np.arange(
    node.parameters.min_amp_factor,
    node.parameters.max_amp_factor,
    node.parameters.amp_factor_step,
)

with program() as drag_calibration:
    I, _, Q, _, n, n_st = qua_declaration(num_qubits=num_qubits)
    state = [declare(bool) for _ in range(num_qubits)]
    state_stream = [declare_stream() for _ in range(num_qubits)]
    a = declare(fixed)  # QUA variable for the qubit drive amplitude pre-factor
    npi = declare(int)  # QUA variable for the number of qubit pulses
    count = declare(int)  # QUA variable for counting the qubit pulses

    for i, qubit in enumerate(qubits):
        # Bring the active qubits to the desired frequency point
        machine.set_all_fluxes(flux_point=flux_point, target=qubit)

        with for_(n, 0, n < n_avg, n + 1):
            save(n, n_st)
            for option in [0, 1]:
                with for_(*from_array(a, amps)):
                    # Initialize the qubits
                    if reset_type == "active":
                        active_reset(qubit, "readout")
                    else:
                        qubit.wait(qubit.thermalization_time * u.ns)

                    if option == 0:
                        play("x180" * amp(1, 0, 0, a), qubit.xy.name)
                        play("y90" * amp(a, 0, 0, 1), qubit.xy.name)
                    else:
                        play("y180" * amp(a, 0, 0, 1), qubit.xy.name)
                        play("x90" * amp(1, 0, 0, a), qubit.xy.name)

                    qubit.align()
                    qubit.resonator.measure("readout", qua_vars=(I[i], Q[i]))
                    assign(state[i], I[i] > qubit.resonator.operations["readout"].threshold)
                    save(state[i], state_stream[i])
        # Measure sequentially
        if not node.parameters.multiplexed:
            align()

    with stream_processing():
        n_st.save("n")
        for i, qubit in enumerate(qubits):
            state_stream[i].boolean_to_int().buffer(len(amps)).buffer(2).average().save(f"state{i + 1}")


# %% {Simulate_or_execute}
if node.parameters.simulate:
    # Simulates the QUA program for the specified duration
    simulation_config = SimulationConfig(duration=node.parameters.simulation_duration_ns * 4)  # In clock cycles = 4ns
    job = qmm.simulate(config, drag_calibration, simulation_config)
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
    with qm_session(qmm, config, timeout=node.parameters.timeout) as qm:
        job = qm.execute(drag_calibration)
        results = fetching_tool(job, ["n"], mode="live")
        while results.is_processing():
            # Fetch results
            n = results.fetch_all()[0]
            # Progress bar
            progress_counter(n, n_avg, start_time=results.start_time)

# %% {Data_fetching_and_dataset_creation}
if not node.parameters.simulate:
    if node.parameters.load_data_id is None:
        # Fetch the data from the OPX and convert it into a xarray with corresponding axes (from most inner to outer loop)
        ds = fetch_results_as_xarray(job.result_handles, qubits, {"amp": amps, "sequence": [0, 1]})
        # Add the qubit pulse absolute alpha coefficient to the dataset
        ds = ds.assign_coords(
            {"alpha": (["qubit", "amp"], np.array([q.xy.operations[operation].alpha * amps for q in qubits]))}
        )
    else:
        node = node.load_from_id(node.parameters.load_data_id)
        ds = node.results["ds"]
    # Add the dataset to the node
    node.results = {"ds": ds}

    # %% {Data_analysis}
    # Perform a linear fit of the qubit state vs DRAG coefficient scaling factor
    state = ds.state
    fitted = xr.polyval(state.amp, state.polyfit(dim="amp", deg=1).polyfit_coefficients)
    # TODO: what does it do? Explain the analysis
    diffs = state.polyfit(dim="amp", deg=1).polyfit_coefficients.diff(dim="sequence").drop("sequence")
    intersection = -diffs.sel(degree=0) / diffs.sel(degree=1)
    intersection_alpha = intersection * xr.DataArray(
        [q.xy.operations[operation].alpha for q in qubits],
        dims=["qubit"],
        coords={"qubit": ds.qubit},
    )

    # Save fitting results
    fit_results = {qubit.name: {"alpha": float(intersection_alpha.sel(qubit=qubit.name).values)} for qubit in qubits}
    for q in qubits:
        print(f"DRAG coefficient for {q.name} is {fit_results[q.name]['alpha']}")
    node.results["fit_results"] = fit_results

    # %% {Plotting}
    grid = QubitGrid(ds, [q.grid_location for q in qubits])
    for ax, qubit in grid_iter(grid):
        ds.loc[qubit].state.plot(ax=ax, x="alpha", hue="sequence")
        ax.axvline(fit_results[qubit["qubit"]]["alpha"], color="r")
        ax.set_ylabel("num. of pulses")
        ax.set_xlabel(r"DRAG coeff $\alpha$")
        ax.set_title(qubit["qubit"])
    grid.fig.suptitle("DRAG calibration")
    plt.tight_layout()
    plt.show()
    node.results["figure"] = grid.fig

    # %% {Update_state}
    # Revert the change done at the beginning of the node
    for qubit in tracked_qubits:
        qubit.revert_changes()
    # Update the state
    if node.parameters.load_data_id is None:
        with node.record_state_updates():
            for q in qubits:
                q.xy.operations[operation].alpha = fit_results[q.name]["alpha"]

        # %% {Save_results}
        node.results["initial_parameters"] = node.parameters.model_dump()
        node.machine = machine
        save_node(node)
