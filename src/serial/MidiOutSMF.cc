#include "MidiOutSMF.hh"

#include "FileOperations.hh"
#include "PlugException.hh"
#include "serialize.hh"

#include "narrow.hh"

#include <algorithm>
#include <array>
#include <cstdio>
#include <span>
#include <string>

namespace openmsx {

static constexpr std::array<uint8_t, 4> END_OF_TRACK = {0x00, 0xFF, 0x2F, 0x00};

static void putVarLen(std::vector<uint8_t>& out, uint64_t value)
{
	// SMF variable-length quantities hold at most 28 bits.
	value = std::min<uint64_t>(value, 0x0FFFFFFF);
	std::array<uint8_t, 4> tmp{};
	size_t n = 0;
	do {
		tmp[n++] = uint8_t(value & 0x7F);
		value >>= 7;
	} while (value);
	while (n--) {
		out.push_back(uint8_t(tmp[n] | (n ? 0x80 : 0x00)));
	}
}

static void putBE32(std::ofstream& file, uint32_t value)
{
	file.put(narrow_cast<char>(value >> 24));
	file.put(narrow_cast<char>(value >> 16));
	file.put(narrow_cast<char>(value >>  8));
	file.put(narrow_cast<char>(value >>  0));
}

static void putBytes(std::ofstream& file, std::span<const uint8_t> data)
{
	file.write(reinterpret_cast<const char*>(data.data()),
	           narrow<std::streamsize>(data.size()));
}

MidiOutSMF::MidiOutSMF(CommandController& commandController)
	: filenameSetting(
		commandController, "midi-out-smf-filename",
		"filename of the Standard MIDI File that midi-out-smf writes",
		"openmsx-midi-out.mid")
{
}

void MidiOutSMF::plugHelper(Connector& /*connector*/, EmuTime time)
{
	FileOperations::openOfStream(
		file, filenameSetting.getString(),
		std::ios::binary | std::ios::out | std::ios::trunc);
	if (file.fail()) {
		file.clear();
		throw PlugException("Error opening SMF file");
	}
	clearBuffer();
	plugTime = time;
	lastTick = 0;

	// Header: format 0, one track.
	static constexpr std::array<uint8_t, 14> header = {
		'M', 'T', 'h', 'd', 0, 0, 0, 6, 0, 0, 0, 1,
		uint8_t(TICKS_PER_QUARTER >> 8), uint8_t(TICKS_PER_QUARTER & 0xFF),
	};
	putBytes(file, header);
	static constexpr std::array<uint8_t, 4> trackId = {'M', 'T', 'r', 'k'};
	putBytes(file, trackId);
	putBE32(file, 0); // patched by flushTrack()
	trackDataStart = file.tellp();
	trackDataEnd = trackDataStart;

	// Tempo, then a marker with the emulated time of tick 0, so that the
	// file can be lined up with other recordings of the same run.
	std::vector<uint8_t> tempo = {
		0xFF, 0x51, 0x03,
		uint8_t(TEMPO_US_PER_QUARTER >> 16),
		uint8_t(TEMPO_US_PER_QUARTER >>  8),
		uint8_t(TEMPO_US_PER_QUARTER >>  0),
	};
	appendEvent(0, tempo);
	std::array<char, 64> text{};
	int len = snprintf(text.data(), text.size(),
	                   "openMSX emutime %.9f", time.toDouble());
	std::vector<uint8_t> marker = {0xFF, 0x06};
	putVarLen(marker, uint64_t(len));
	marker.insert(marker.end(), text.data(), text.data() + len);
	appendEvent(0, marker);
	flushTrack();
}

void MidiOutSMF::unplugHelper(EmuTime /*time*/)
{
	if (file.is_open()) {
		flushTrack();
		file.close();
	}
}

zstring_view MidiOutSMF::getName() const
{
	return "midi-out-smf";
}

zstring_view MidiOutSMF::getDescription() const
{
	return "MIDI output to a Standard MIDI File. Each message is stamped "
	       "with the emulated time since the pluggable was plugged in "
	       "(1 tick = 20 us), so the timing is kept when the emulator runs "
	       "unthrottled. The filename is set with the "
	       "'midi-out-smf-filename' setting.";
}

uint64_t MidiOutSMF::ticksSincePlug(EmuTime time) const
{
	if (time < plugTime) return 0; // e.g. after a reverse or a loadstate
	uint64_t d = (time - plugTime).toUint64();
	return (d / MAIN_FREQ) * TICKS_PER_SECOND +
	       (d % MAIN_FREQ) * TICKS_PER_SECOND / MAIN_FREQ;
}

void MidiOutSMF::appendEvent(uint64_t tick, const std::vector<uint8_t>& data)
{
	std::vector<uint8_t> event;
	putVarLen(event, tick > lastTick ? tick - lastTick : 0);
	if (tick > lastTick) lastTick = tick;
	event.insert(event.end(), data.begin(), data.end());
	file.seekp(trackDataEnd);
	putBytes(file, event);
	trackDataEnd = file.tellp();
}

void MidiOutSMF::flushTrack()
{
	// Keep the file a complete SMF: end-of-track after the last event and
	// the track length in the chunk header. The next event overwrites both.
	file.seekp(trackDataEnd);
	putBytes(file, END_OF_TRACK);
	file.seekp(trackDataStart - 4);
	putBE32(file, narrow<uint32_t>(trackDataEnd - trackDataStart + END_OF_TRACK.size()));
	file.flush();
}

void MidiOutSMF::recvMessage(const std::vector<uint8_t>& message, EmuTime time)
{
	if (!file.is_open() || message.empty()) return;

	std::vector<uint8_t> data;
	uint8_t status = message[0];
	if (status == 0xF0) {
		// SysEx (possibly the first chunk of a long one): F0 <len> <rest>
		data.push_back(0xF0);
		putVarLen(data, message.size() - 1);
		data.insert(data.end(), message.begin() + 1, message.end());
	} else if (status < 0x80) {
		// Continuation chunk of a long SysEx: escape as F7 <len> <bytes>
		data.push_back(0xF7);
		putVarLen(data, message.size());
		data.insert(data.end(), message.begin(), message.end());
	} else if (status >= 0xF8) {
		// Realtime messages (clock, active sensing, reset) have no place
		// in a track: 0xFF would read as a meta event.
		return;
	} else if (status >= 0xF1) {
		// Other system common messages: escape as F7 <len> <bytes>
		data.push_back(0xF7);
		putVarLen(data, message.size());
		data.insert(data.end(), message.begin(), message.end());
	} else {
		// Channel message; always written with its status byte.
		data = message;
	}
	appendEvent(ticksSincePlug(time), data);
	flushTrack();
}

template<typename Archive>
void MidiOutSMF::serialize(Archive& /*ar*/, unsigned /*version*/)
{
	// don't try to resume a previous file (see MidiOutLogger)
}
INSTANTIATE_SERIALIZE_METHODS(MidiOutSMF);
REGISTER_POLYMORPHIC_INITIALIZER(Pluggable, MidiOutSMF, "MidiOutSMF");

} // namespace openmsx
