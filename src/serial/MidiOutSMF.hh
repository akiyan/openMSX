#ifndef MIDIOUTSMF_HH
#define MIDIOUTSMF_HH

#include "MidiOutDevice.hh"

#include "EmuTime.hh"
#include "FilenameSetting.hh"

#include <cstdint>
#include <fstream>
#include <vector>

namespace openmsx {

class CommandController;

/** MIDI out pluggable that writes a Standard MIDI File (format 0).
  *
  * Every message is stamped with the emulated time at which its last byte
  * arrived, counted from the moment the pluggable was plugged in. Because the
  * timestamps come from EmuTime and not from the host clock, the file keeps
  * the musical timing even when the emulator runs unthrottled.
  *
  * Timing: 25000 ticks per quarter note at the default tempo of 120 bpm,
  * so one tick is 20 microseconds (50000 ticks per emulated second).
  *
  * The file stays a valid SMF after every message (the end-of-track event
  * and the track length are rewritten each time), so it can be read while
  * the emulator is still running or after it was killed.
  */
class MidiOutSMF final : public MidiOutDevice
{
public:
	static constexpr uint16_t TICKS_PER_QUARTER = 25000;
	static constexpr uint32_t TEMPO_US_PER_QUARTER = 500000;
	static constexpr uint64_t TICKS_PER_SECOND = 50000;

	explicit MidiOutSMF(CommandController& commandController);

	// Pluggable
	void plugHelper(Connector& connector, EmuTime time) override;
	void unplugHelper(EmuTime time) override;
	[[nodiscard]] zstring_view getName() const override;
	[[nodiscard]] zstring_view getDescription() const override;

	template<typename Archive>
	void serialize(Archive& ar, unsigned version);

protected:
	// MidiOutDevice
	void recvMessage(const std::vector<uint8_t>& message, EmuTime time) override;

private:
	[[nodiscard]] uint64_t ticksSincePlug(EmuTime time) const;
	void appendEvent(uint64_t tick, const std::vector<uint8_t>& data);
	void flushTrack();

	FilenameSetting filenameSetting;
	std::ofstream file;
	EmuTime plugTime = EmuTime::zero();
	uint64_t lastTick = 0;
	std::streamoff trackDataStart = 0;
	std::streamoff trackDataEnd = 0;
};

} // namespace openmsx

#endif
