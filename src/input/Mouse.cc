// $Id$

#include "Mouse.hh"
#include "MSXEventDistributor.hh"
#include "InputEvents.hh"
#include "checked_cast.hh"
#include "serialize.hh"
#include "serialize_meta.hh"
#include "unreachable.hh"

using std::string;

namespace openmsx {

const int TRESHOLD = 2;
const int SCALE = 2;
const int FAZE_XHIGH = 0;
const int FAZE_XLOW  = 1;
const int FAZE_YHIGH = 2;
const int FAZE_YLOW  = 3;
const int STROBE = 0x04;


Mouse::Mouse(MSXEventDistributor& eventDistributor_)
	: eventDistributor(eventDistributor_)
	, lastTime(EmuTime::zero)
{
	status = JOY_BUTTONA | JOY_BUTTONB;
	faze = FAZE_YLOW;
	xrel = yrel = curxrel = curyrel = 0;
	mouseMode = true;
}

Mouse::~Mouse()
{
	if (isPluggedIn()) {
		Mouse::unplugHelper(EmuTime::dummy());
	}
}


// Pluggable
const string& Mouse::getName() const
{
	static const string name("mouse");
	return name;
}

const string& Mouse::getDescription() const
{
	static const string desc("MSX mouse.");
	return desc;
}

void Mouse::plugHelper(Connector& /*connector*/, EmuTime::param time)
{
	if (status & JOY_BUTTONA) {
		// not pressed, mouse mode
		mouseMode = true;
		lastTime.advance(time);
	} else {
		// left mouse button pressed, joystick emulation mode
		mouseMode = false;
	}
	plugHelper2();
}

void Mouse::plugHelper2()
{
	eventDistributor.registerEventListener(*this);
}

void Mouse::unplugHelper(EmuTime::param /*time*/)
{
	eventDistributor.unregisterEventListener(*this);
}


// JoystickDevice
byte Mouse::read(EmuTime::param /*time*/)
{
	if (mouseMode) {
		switch (faze) {
		case FAZE_XHIGH:
			return (((xrel / SCALE) >> 4) & 0x0F) | status;
		case FAZE_XLOW:
			return  ((xrel / SCALE)       & 0x0F) | status;
		case FAZE_YHIGH:
			return (((yrel / SCALE) >> 4) & 0x0F) | status;
		case FAZE_YLOW:
			return  ((yrel / SCALE)       & 0x0F) | status;
		default:
			UNREACHABLE; return 0;
		}
	} else {
		emulateJoystick();
		return status;
	}
}

void Mouse::emulateJoystick()
{
	status &= ~(JOY_UP | JOY_DOWN | JOY_LEFT | JOY_RIGHT);

	int deltax = curxrel; curxrel = 0;
	int deltay = curyrel; curyrel = 0;
	int absx = (deltax > 0) ? deltax : -deltax;
	int absy = (deltay > 0) ? deltay : -deltay;

	if ((absx < TRESHOLD) && (absy < TRESHOLD)) {
		return;
	}

	// tan(pi/8) ~= 5/12
	if (deltax > 0) {
		if (deltay > 0) {
			if ((12 * absx) > (5 * absy)) {
				status |= JOY_RIGHT;
			}
			if ((12 * absy) > (5 * absx)) {
				status |= JOY_DOWN;
			}
		} else {
			if ((12 * absx) > (5 * absy)) {
				status |= JOY_RIGHT;
			}
			if ((12 * absy) > (5 * absx)) {
				status |= JOY_UP;
			}
		}
	} else {
		if (deltay > 0) {
			if ((12 * absx) > (5 * absy)) {
				status |= JOY_LEFT;
			}
			if ((12 * absy) > (5 * absx)) {
				status |= JOY_DOWN;
			}
		} else {
			if ((12 * absx) > (5 * absy)) {
				status |= JOY_LEFT;
			}
			if ((12 * absy) > (5 * absx)) {
				status |= JOY_UP;
			}
		}
	}
}

void Mouse::write(byte value, EmuTime::param time)
{
	if (mouseMode) {
		// TODO figure out the timeout mechanism
		//      does it exist at all?

		const int TIMEOUT = 1000; // TODO find a good value
		int delta = lastTime.getTicksTill(time);
		lastTime.advance(time);
		if (delta >= TIMEOUT) {
			faze = FAZE_YLOW;
		}

		switch (faze) {
		case FAZE_XHIGH:
			if ((value & STROBE) == 0) faze = FAZE_XLOW;
			break;
		case FAZE_XLOW:
			if ((value & STROBE) != 0) faze = FAZE_YHIGH;
			break;
		case FAZE_YHIGH:
			if ((value & STROBE) == 0) faze = FAZE_YLOW;
			break;
		case FAZE_YLOW:
			if ((value & STROBE) != 0) {
				faze = FAZE_XHIGH;
				xrel = curxrel; yrel = curyrel;
				curxrel = 0; curyrel = 0;
			}
			break;
		}
	} else {
		// ignore
	}
}


// EventListener
void Mouse::signalEvent(shared_ptr<const Event> event, EmuTime::param /*time*/)
{
	switch (event->getType()) {
	case OPENMSX_MOUSE_MOTION_EVENT: {
		const MouseMotionEvent& motionEvent =
			checked_cast<const MouseMotionEvent&>(*event);
		curxrel -= motionEvent.getX();
		curyrel -= motionEvent.getY();
		if (curxrel >  127 * SCALE) curxrel =  127 * SCALE;
		if (curxrel < -128 * SCALE) curxrel = -128 * SCALE;
		if (curyrel >  127 * SCALE) curyrel =  127 * SCALE;
		if (curyrel < -128 * SCALE) curyrel = -128 * SCALE;
		break;
	}
	case OPENMSX_MOUSE_BUTTON_DOWN_EVENT: {
		const MouseButtonEvent& buttonEvent =
			checked_cast<const MouseButtonEvent&>(*event);
		switch (buttonEvent.getButton()) {
		case MouseButtonEvent::LEFT:
			status &= ~JOY_BUTTONA;
			break;
		case MouseButtonEvent::RIGHT:
			status &= ~JOY_BUTTONB;
			break;
		default:
			// ignore other buttons
			break;
		}
		break;
	}
	case OPENMSX_MOUSE_BUTTON_UP_EVENT: {
		const MouseButtonEvent& buttonEvent =
			checked_cast<const MouseButtonEvent&>(*event);
		switch (buttonEvent.getButton()) {
		case MouseButtonEvent::LEFT:
			status |= JOY_BUTTONA;
			break;
		case MouseButtonEvent::RIGHT:
			status |= JOY_BUTTONB;
			break;
		default:
			// ignore other buttons
			break;
		}
		break;
	}
	default:
		// ignore
		break;
	}
}

// version 1: Initial version, the variables curxrel, curyrel and status were
//            not serialized.
// version 2: Also serialize the above variables, this is required for
//            record/replay, see comment in Keyboard.cc for more details.
template<typename Archive>
void Mouse::serialize(Archive& ar, unsigned version)
{
	ar.serialize("lastTime", lastTime);
	ar.serialize("faze", faze);
	ar.serialize("xrel", xrel);
	ar.serialize("yrel", yrel);
	ar.serialize("mouseMode", mouseMode);
	if (version >= 2) {
		ar.serialize("curxrel", curxrel);
		ar.serialize("curyrel", curyrel);
		ar.serialize("status",  status);
	}
	if (ar.isLoader() && isPluggedIn()) {
		plugHelper2();
	}
}
INSTANTIATE_SERIALIZE_METHODS(Mouse);
REGISTER_POLYMORPHIC_INITIALIZER(Pluggable, Mouse, "Mouse");

} // namespace openmsx
