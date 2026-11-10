# Nozzle-touch -> probe Z offset calibration for Klipper
#
# Install: copy to ~/klipper/klippy/extras/nozzle_probe_calibrate.py
# and restart Klipper.
#
# Example config:
#
#   [nozzle_probe_calibrate]
#   nozzle_pin: ^!PG11            # input pin of the external nozzle-touch sensor
#   nozzle_position: 10, 20       # XY of the *nozzle* when touching that sensor
#   #probe_position:              # XY where the *probe* is measured (test spot).
#                                 # Default: same as nozzle_position
#   #probe_pin:                   # default: reuse the pin of [probe]
#   #x_offset:                    # default: [probe] x_offset
#   #y_offset:                    # default: [probe] y_offset
#   #samples:                     # default: [probe] samples (or 1)
#   #samples_tolerance:           # max allowed spread between samples (optional)
#   #sample_retract_dist:         # default: [probe] sample_retract_dist (or 2)
#   #speed:                       # default: [probe] speed (or 5)
#   #lift_speed:                  # default: [probe] lift_speed (or speed)
#   #travel_speed: 150
#   #travel_z: 10                 # Z used for XY travel / start of probing
#   #probe_depth: 15              # max distance to move down from travel_z
#   #extra_offset: 0.0            # manual correction, added to the result
#
# G-code:
#   NOZZLE_PROBE_CALIBRATE [SAMPLES=n] [EXTRA_OFFSET=mm] [APPLY=1] [SAVE=0]
#   SET_NOZZLE_PROBE_EXTRA VALUE=mm
#
# z_offset = probe_trigger_z - nozzle_trigger_z + extra_offset
#
# A positive extra_offset raises z_offset, which makes the nozzle print
# closer to the bed by that amount (same as a larger [probe] z_offset).


class NozzleProbeCalibrate:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.name = config.get_name()

        # Required
        self.nozzle_pos = config.getfloatlist('nozzle_position', count=2)
        # Optional (None -> fall back to [probe] at run time)
        self.probe_pos = config.getfloatlist('probe_position', None, count=2)
        self.x_offset = config.getfloat('x_offset', None)
        self.y_offset = config.getfloat('y_offset', None)
        self.samples = config.getint('samples', None, minval=1)
        self.tolerance = config.getfloat('samples_tolerance', None,
                                         minval=0.)
        self.retract_dist = config.getfloat('sample_retract_dist', None,
                                            above=0.)
        self.speed = config.getfloat('speed', None, above=0.)
        self.lift_speed = config.getfloat('lift_speed', None, above=0.)
        self.travel_speed = config.getfloat('travel_speed', 150., above=0.)
        self.travel_z = config.getfloat('travel_z', 10.)
        self.probe_depth = config.getfloat('probe_depth', 15., above=0.)
        self.extra_offset = config.getfloat('extra_offset', 0.)

        # Pins
        self.nozzle_endstop = self._setup_endstop(
            config.get('nozzle_pin'), 'nozzle_probe_calibrate_nozzle')
        probe_pin = config.get('probe_pin', None)
        self.probe_endstop = None
        if probe_pin is not None:
            # Shares the pin with [probe] if it is the same one
            self.probe_endstop = self._setup_endstop(
                probe_pin, 'nozzle_probe_calibrate_probe')
        self.printer.register_event_handler('klippy:mcu_identify',
                                            self._handle_mcu_identify)

        self.gcode.register_command(
            'NOZZLE_PROBE_CALIBRATE', self.cmd_NOZZLE_PROBE_CALIBRATE,
            desc=self.cmd_NOZZLE_PROBE_CALIBRATE_help)
        self.gcode.register_command(
            'SET_NOZZLE_PROBE_EXTRA', self.cmd_SET_NOZZLE_PROBE_EXTRA,
            desc="Set the manual extra offset used by NOZZLE_PROBE_CALIBRATE")

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------
    def _setup_endstop(self, pin_desc, share_type):
        ppins = self.printer.lookup_object('pins')
        pin_params = ppins.lookup_pin(pin_desc, can_invert=True,
                                      can_pullup=True, share_type=share_type)
        mcu = pin_params['chip']
        mcu_endstop = mcu.setup_pin('endstop', pin_params)
        return mcu_endstop

    def _handle_mcu_identify(self):
        # Attach the Z steppers so a trigger stops Z motion
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        endstops = [self.nozzle_endstop]
        if self.probe_endstop is not None:
            endstops.append(self.probe_endstop)
        for stepper in kin.get_steppers():
            if stepper.is_active_axis('z'):
                for es in endstops:
                    es.add_stepper(stepper)

    # ------------------------------------------------------------------
    # [probe] fallbacks (resolved lazily so config order doesn't matter)
    # ------------------------------------------------------------------
    def _get_probe(self):
        return self.printer.lookup_object('probe', None)

    def _probe_param(self, key, attr, default):
        probe = self._get_probe()
        if probe is None:
            return default
        try:
            return probe.get_probe_params()[key]
        except Exception:
            pass
        val = getattr(probe, attr, None)
        return default if val is None else val

    def _resolve_probe_offsets(self):
        xo, yo = self.x_offset, self.y_offset
        if xo is None or yo is None:
            probe = self._get_probe()
            if probe is None:
                raise self.printer.command_error(
                    "%s: x_offset/y_offset not set and no [probe] section "
                    "to take them from" % (self.name,))
            offs = probe.get_offsets()
            if xo is None:
                xo = offs[0]
            if yo is None:
                yo = offs[1]
        return xo, yo

    def _get_probe_endstop(self):
        if self.probe_endstop is not None:
            return self.probe_endstop
        probe = self._get_probe()
        if probe is None:
            raise self.printer.command_error(
                "%s: probe_pin not set and no [probe] section found"
                % (self.name,))
        return probe.mcu_probe

    def _apply_z_offset(self, z_offset):
        probe = self._get_probe()
        if probe is None:
            raise self.printer.command_error("No [probe] section to apply to")
        helper = getattr(probe, 'probe_offsets', None)  # newer Klipper
        if helper is not None and hasattr(helper, 'z_offset'):
            helper.z_offset = z_offset
        elif hasattr(probe, 'z_offset'):                # older Klipper
            probe.z_offset = z_offset
        else:
            raise self.printer.command_error(
                "Unable to set z_offset on this version of [probe]")

    # ------------------------------------------------------------------
    # Probing
    # ------------------------------------------------------------------
    def _measure(self, gcmd, toolhead, endstop, x, y, samples, speed,
                 lift_speed, retract, label):
        phoming = self.printer.lookup_object('homing')
        # Travel to the spot at safe height
        toolhead.manual_move([None, None, self.travel_z], lift_speed)
        toolhead.manual_move([x, y, None], self.travel_speed)
        results = []
        for i in range(samples):
            pos = toolhead.get_position()
            pos[2] = self.travel_z - self.probe_depth
            epos = phoming.probing_move(endstop, pos, speed)
            z = epos[2]
            results.append(z)
            gcmd.respond_info("%s sample %d/%d: z=%.4f"
                              % (label, i + 1, samples, z))
            toolhead.manual_move([None, None, min(z + retract,
                                                  self.travel_z)], lift_speed)
        spread = max(results) - min(results)
        if self.tolerance is not None and spread > self.tolerance:
            raise gcmd.error(
                "%s samples exceed tolerance: spread %.4f > %.4f"
                % (label, spread, self.tolerance))
        avg = sum(results) / len(results)
        gcmd.respond_info("%s result: %.4f (spread %.4f)"
                          % (label, avg, spread))
        return avg

    cmd_NOZZLE_PROBE_CALIBRATE_help = (
        "Touch nozzle sensor, probe the test spot, and set probe z_offset")

    def cmd_NOZZLE_PROBE_CALIBRATE(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        homed = toolhead.get_status(
            self.printer.get_reactor().monotonic())['homed_axes']
        if 'x' not in homed or 'y' not in homed or 'z' not in homed:
            raise gcmd.error("Home all axes before NOZZLE_PROBE_CALIBRATE")

        default_samples = self.samples
        if default_samples is None:
            default_samples = self._probe_param('samples', 'sample_count', 1)
        samples = gcmd.get_int('SAMPLES', default_samples, minval=1)
        extra = gcmd.get_float('EXTRA_OFFSET', self.extra_offset)
        apply_ = gcmd.get_int('APPLY', 1)
        save = gcmd.get_int('SAVE', 0)

        speed = self.speed
        if speed is None:
            speed = self._probe_param('probe_speed', 'speed', 5.)
        lift_speed = self.lift_speed
        if lift_speed is None:
            lift_speed = self._probe_param('lift_speed', 'lift_speed', speed)
        retract = self.retract_dist
        if retract is None:
            retract = self._probe_param('sample_retract_dist',
                                        'sample_retract_dist', 2.)

        xo, yo = self._resolve_probe_offsets()
        spot = self.probe_pos if self.probe_pos is not None \
            else self.nozzle_pos
        # Nozzle position that puts the probe over the test spot
        probe_nozzle_x = spot[0] - xo
        probe_nozzle_y = spot[1] - yo

        nozzle_z = self._measure(
            gcmd, toolhead, self.nozzle_endstop,
            self.nozzle_pos[0], self.nozzle_pos[1],
            samples, speed, lift_speed, retract, "nozzle")
        probe_z = self._measure(
            gcmd, toolhead, self._get_probe_endstop(),
            probe_nozzle_x, probe_nozzle_y,
            samples, speed, lift_speed, retract, "probe")

        toolhead.manual_move([None, None, self.travel_z], lift_speed)
        toolhead.wait_moves()

        z_offset = probe_z - nozzle_z + extra
        gcmd.respond_info(
            "nozzle_z=%.4f probe_z=%.4f extra=%.4f -> probe z_offset=%.4f"
            % (nozzle_z, probe_z, extra, z_offset))

        if apply_:
            self._apply_z_offset(z_offset)
            gcmd.respond_info("Applied probe z_offset %.4f for this session"
                              % (z_offset,))
        if save:
            configfile = self.printer.lookup_object('configfile')
            configfile.set('probe', 'z_offset', "%.3f" % (z_offset,))
            gcmd.respond_info("probe z_offset staged; run SAVE_CONFIG to "
                              "make it permanent (restarts Klipper)")

    def cmd_SET_NOZZLE_PROBE_EXTRA(self, gcmd):
        self.extra_offset = gcmd.get_float('VALUE')
        gcmd.respond_info("nozzle_probe_calibrate extra_offset = %.4f"
                          % (self.extra_offset,))

    def get_status(self, eventtime):
        return {'extra_offset': self.extra_offset}


def load_config(config):
    return NozzleProbeCalibrate(config)
