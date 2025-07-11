"""
        TIME OF FLIGHT
This sequence involves sending a readout pulse and capturing the raw ADC traces.
The data undergoes post-processing to calibrate three distinct parameters:
    - Time of Flight: This represents the internal processing time and the propagation delay of the readout pulse.
    Its value can be adjusted in the configuration under "time_of_flight".
    This value is utilized to offset the acquisition window relative to when the readout pulse is dispatched.

    - Analog Inputs Offset: Due to minor impedance mismatches, the signals captured by the OPX might exhibit slight offsets.

    - Analog Inputs Gain: If a signal is constrained by digitization or if it saturates the ADC,
    the variable gain of the OPX analog input, ranging from -12 dB to 20 dB, can be modified to fit the signal within the ADC range of +/-0.5V.
"""
# %%
from qualibrate import QualibrationNode, NodeParameters
from iqcc_research.quam_config.components import Quam
from iqcc_research.quam_config.lib.plot_utils import QubitGrid, grid_iter
from iqcc_research.quam_config.lib.save_utils import fetch_results_as_xarray, load_dataset, save_node 
from iqcc_research.quam_config.trackable_object import tracked_updates
from qualang_tools.multi_user import qm_session
from qualang_tools.units import unit
from qm import SimulationConfig
from qm.qua import *
from typing import Optional, List
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import savgol_filter


# %% {Node_parameters}
class Parameters(NodeParameters):

    qubits: Optional[List[str]] = None
    num_averages: int = 100
    time_of_flight_in_ns: Optional[int] = 24
    intermediate_frequency_in_mhz: Optional[float] = 50
    readout_amplitude_in_v: Optional[float] = 0.1
    readout_length_in_ns: Optional[int] = None
    simulate: bool = False
    simulation_duration_ns: int = 2500
    timeout: int = 100


node = QualibrationNode(name="01b_Time_of_Flight", parameters=Parameters())


# %% {Initialize_QuAM_and_QOP}
# Class containing tools to help handling units and conversions.
u = unit(coerce_to_integer=True)
# Instantiate the QuAM class from the state file
machine = Quam.load()

# Get the relevant QuAM components
if node.parameters.qubits is None or node.parameters.qubits == "":
    qubits = machine.active_qubits
else:
    qubits = [machine.qubits[q] for q in node.parameters.qubits]
resonators = [qubit.resonator for qubit in qubits]
num_qubits = len(qubits)

tracked_resonators = []
for q in qubits:
    resonator = q.resonator
    # make temporary updates before running the program and revert at the end.
    with tracked_updates(resonator, auto_revert=False, dont_assign_to_none=True) as resonator:
        resonator.time_of_flight = node.parameters.time_of_flight_in_ns
        resonator.operations["readout"].length = node.parameters.readout_length_in_ns
        resonator.operations["readout"].amplitude = node.parameters.readout_amplitude_in_v
        if node.parameters.intermediate_frequency_in_mhz is not None:
            resonator.intermediate_frequency = node.parameters.intermediate_frequency_in_mhz * u.MHz
        tracked_resonators.append(resonator)

# Generate the OPX and Octave configurations
config = machine.generate_config()
# Open Communication with the QOP
qmm = machine.connect()


# %% {QUA_program}
with program() as raw_trace_prog:
    n = declare(int)  # QUA variable for the averaging loop
    adc_st = [declare_stream(adc_trace=True) for _ in range(len(resonators))]  # The stream to store the raw ADC trace

    for i, rr in enumerate(resonators):
        with for_(n, 0, n < node.parameters.num_averages, n + 1):
            # Reset the phase of the digital oscillator associated to the resonator element. Needed to average the cosine signal.
            reset_phase(rr.name)
            # Measure the resonator (send a readout pulse and record the raw ADC trace)
            rr.measure("readout", stream=adc_st[i])
            # Wait for the resonator to deplete
            rr.wait(machine.depletion_time * u.ns)
        # Measure sequentially
        align(*[rr.name for rr in resonators])

    with stream_processing():
        for i in range(num_qubits):
            # Will save average:
            adc_st[i].input1().average().save(f"adcI{i + 1}")
            adc_st[i].input2().average().save(f"adcQ{i + 1}")
            # Will save only last run:
            adc_st[i].input1().save(f"adc_single_runI{i + 1}")
            adc_st[i].input2().save(f"adc_single_runQ{i + 1}")


# %% {Simulate_or_execute}
if node.parameters.simulate:
    # Simulates the QUA program for the specified duration
    simulation_config = SimulationConfig(duration=node.parameters.simulation_duration_ns * 4)  # In clock cycles = 4ns
    # Simulate blocks python until the simulation is done
    job = qmm.simulate(config, raw_trace_prog, simulation_config)
    # Plot the simulated samples
    samples = job.get_simulated_samples()
    fig, ax = plt.subplots(nrows=len(samples.keys()), sharex=True)
    for i, con in enumerate(samples.keys()):
        plt.subplot(len(samples.keys()),1,i+1)
        samples[con].plot()
        plt.title(con)
    plt.tight_layout()
    # save the figure
    node.results = {"figure": plt.gcf()}
else:
    with qm_session(qmm, config, timeout=node.parameters.timeout) as qm:
        # Send the QUA program to the OPX, which compiles and executes it
        job = qm.execute(raw_trace_prog)
        # Creates a result handle to fetch data from the OPX
        res_handles = job.result_handles
        # Waits (blocks the Python console) until all results have been acquired
        res_handles.wait_for_all_values()

    # %% {Data_fetching_and_dataset_creation}
    # Fetch the data from the OPX and convert it into a xarray with corresponding axes (from most inner to outer loop)
    time_axis = np.linspace(0, resonators[0].operations["readout"].length, resonators[0].operations["readout"].length)
    ds = fetch_results_as_xarray(job.result_handles, qubits, {"time": time_axis})
    # Convert raw ADC traces into volts
    ds = ds.assign({key: -ds[key] / 2**12 for key in ("adcI", "adcQ", "adc_single_runI", "adc_single_runQ")})
    ds = ds.assign({"IQ_abs": np.sqrt(ds["adcI"] ** 2 + ds["adcQ"] ** 2)})
    # Add the dataset to the node
    node.results = {"ds": ds}

    # %% {Data_analysis}
    # Filter the data to get the pulse arrival time
    filtered_adc = savgol_filter(np.abs(ds.IQ_abs), 11, 3)
    # Detect the arrival of the readout signal
    delays = []
    fit_results = {}
    for q in qubits:
        fit_results[q.name] = {}
        # Filter the time trace to easily find the rising edge of the readout pulse
        filtered_adc = savgol_filter(np.abs(ds.sel(qubit=q.name).IQ_abs), 11, 3)
        # Get a threshold to detect the rising edge
        th = (np.mean(filtered_adc[:100]) + np.mean(filtered_adc[:-100])) / 2
        delay = np.where(filtered_adc > th)[0][0]
        # Find the closest multiple integer of 4ns
        delay = np.round(delay / 4) * 4
        delays.append(delay)
        fit_results[q.name]["delay_to_add"] = delay
        fit_results[q.name]["fit_successful"] = True
    node.results["fit_results"] = fit_results
    # Add the delays to the dataset
    ds = ds.assign_coords({"delays": (["qubit"], delays)})
    ds = ds.assign_coords({"offsets_I": ds.adcI.mean(dim="time")})
    ds = ds.assign_coords({"offsets_Q": ds.adcQ.mean(dim="time")})
    ds = ds.assign_coords(
        {"con": (["qubit"], [machine.qubits[q.name].resonator.opx_input_I.controller_id for q in qubits])}
    )

    # %% {Plotting}
    # Single run
    grid = QubitGrid(ds, [q.grid_location for q in qubits])
    for ax, qubit in grid_iter(grid):
        ds.loc[qubit].adc_single_runI.plot(ax=ax, x="time", label="I", color="b")
        ds.loc[qubit].adc_single_runQ.plot(ax=ax, x="time", label="Q", color="r")
        ax.axvline(ds.loc[qubit].delays, color="k", linestyle="--", label="TOF")
        ax.axhline(ds.loc[qubit].offsets_I, color="b", linestyle="--")
        ax.axhline(ds.loc[qubit].offsets_Q, color="r", linestyle="--")
        ax.fill_between(range(ds.sizes["time"]), -0.5, 0.5, color="grey", alpha=0.2, label="ADC Range")
        ax.set_xlabel("Time [ns]")
        ax.set_ylabel("Readout amplitude [mV]")
        ax.set_title(qubit["qubit"])
    grid.fig.suptitle("Single run")
    plt.tight_layout()
    plt.legend(loc="upper right", ncols=4, bbox_to_anchor=(0.5, 1.35))
    node.results["adc_single_run"] = grid.fig

    # Averaged run
    grid = QubitGrid(ds, [q.grid_location for q in qubits])
    for ax, qubit in grid_iter(grid):
        ds.loc[qubit].adcI.plot(ax=ax, x="time", label="I", color="b")
        ds.loc[qubit].adcQ.plot(ax=ax, x="time", label="Q", color="r")
        ax.axvline(ds.loc[qubit].delays, color="k", linestyle="--", label="TOF")
        ax.axhline(ds.loc[qubit].offsets_I, color="b", linestyle="--")
        ax.axhline(ds.loc[qubit].offsets_Q, color="r", linestyle="--")
        ax.set_xlabel("Time [ns]")
        ax.set_ylabel("Readout amplitude [mV]")
        ax.set_title(qubit["qubit"])
    grid.fig.suptitle("Averaged run")
    plt.tight_layout()
    plt.legend(loc="upper right", ncols=4, bbox_to_anchor=(0.5, 1.35))
    node.results["adc_averaged"] = grid.fig

    # %% {Update_state}
    print(f"Time Of Flight to add: {delays} ns")
    print(f"Offsets to add for I: {ds.offsets_I.values * 1000} mV")
    print(f"Offsets to add for Q: {ds.offsets_Q.values * 1000} mV")

    # Revert the change done at the beginning of the node
    for resonator in tracked_resonators:
        resonator.revert_changes()

    with node.record_state_updates():
        for q in qubits:
            if node.parameters.time_of_flight_in_ns is not None:
                q.resonator.time_of_flight = node.parameters.time_of_flight_in_ns + int(ds.sel(qubit=q.name).delays)
            else:
                q.resonator.time_of_flight += int(ds.sel(qubit=q.name).delays)

        # Update the offsets per controller for each qubit
        for con in np.unique(ds.con.values):
            for i, q in enumerate(ds.where(ds.con == con).qubit.values):
                # Only add the offsets once,
                if i == 0:
                    if machine.qubits[q].resonator.opx_input_I.offset is not None:
                        machine.qubits[q].resonator.opx_input_I.offset += float(
                            ds.where(ds.con == con).offsets_I.mean(dim="qubit").values
                        )
                    else:
                        machine.qubits[q].resonator.opx_input_I.offset = float(
                            ds.where(ds.con == con).offsets_I.mean(dim="qubit").values
                        )
                    if machine.qubits[q].resonator.opx_input_Q.offset is not None:
                        machine.qubits[q].resonator.opx_input_Q.offset += float(
                            ds.where(ds.con == con).offsets_Q.mean(dim="qubit").values
                        )
                    else:
                        machine.qubits[q].resonator.opx_input_Q.offset = float(
                            ds.where(ds.con == con).offsets_Q.mean(dim="qubit").values
                        )
                # else copy the values from the updated qubit
                else:
                    machine.qubits[q].resonator.opx_input_I.offset = machine.qubits[
                        ds.where(ds.con == con).qubit.values[0]
                    ].resonator.opx_input_I.offset
                    machine.qubits[q].resonator.opx_input_Q.offset = machine.qubits[
                        ds.where(ds.con == con).qubit.values[0]
                    ].resonator.opx_input_Q.offset

    # %% {Save_results}
    node.outcomes = {rr.name: "successful" for rr in resonators}
    node.results["ds"] = ds
    node.results["initial_parameters"] = node.parameters.model_dump()
    node.machine = machine
    save_node(node)

