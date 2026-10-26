# Privacy profiles.  Each profile only sets defaults, so an individual
# setting chosen in Blueprint or host.nix always wins.  docs/SECURITY.md
# describes exactly what each profile does and does not protect against.
{ config, lib, ... }:

let
  inherit (lib) mkDefault mkIf mkOption types;
  cfg = config.zed.privacy;
in
{
  options.zed.privacy.profile = mkOption {
    type = types.enum [
      "standard"
      "private"
      "custom"
    ];
    default = "standard";
    description = ''
      standard: Zed's secure defaults (firewall on, nothing listening, no
      telemetry).  private: additionally randomises MAC addresses per
      connection, keeps Bluetooth powered off at boot, stops sending the host
      name in DHCP requests and prefers IPv6 temporary addresses.  custom: no
      profile defaults.  No profile anonymises network traffic.
    '';
  };

  config = mkIf (cfg.profile == "private") {
    networking.networkmanager.wifi.macAddress = mkDefault "random";
    networking.networkmanager.ethernet.macAddress = mkDefault "random";
    networking.networkmanager.wifi.scanRandMacAddress = mkDefault true;
    hardware.bluetooth.powerOnBoot = mkDefault false;
    networking.tempAddresses = mkDefault "default";
    networking.networkmanager.settings.connection = {
      "ipv4.dhcp-send-hostname" = mkDefault false;
      "ipv6.dhcp-send-hostname" = mkDefault false;
      "ipv6.ip6-privacy" = mkDefault 2;
    };
  };
}
