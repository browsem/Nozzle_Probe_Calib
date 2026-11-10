# Nozzle_Probe_Calib
An aigenerated python script to help me home my printer

Specifically i should be able to define what input to use for homing what.

NOZZLE_PROBE_CALIBRATE [SAMPLES=n] [EXTRA_OFFSET=mm] [APPLY=1] [SAVE=0]

To install link the files to klippers folders

cd PathToWhereTheFilesAre

cp ./NOZZLE_PROBE_CALIBRATE.py" "~/klipper/klippy/extras/NOZZLE_PROBE_CALIBRATE.py"

cp  ".nozzle_probe_calibrate.cfg" "~/printer_data/config/nozzle_probe_calibrate.cfg"


add 


include nozzle_probe_calibrate.cfg

to printer.cfg
