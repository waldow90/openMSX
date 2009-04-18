# $Id$
# Replacement for autoconf.
# Performs some test compiles, to check for headers and functions.
# It does not execute anything it builds, making it friendly for cross compiles.

from compilers import CompileCommand, LinkCommand, tryCompile, tryLink
from makeutils import evalMakeExpr, extractMakeVariables
from outpututils import rewriteIfChanged
from probe_results import iterProbeResults

from msysutils import msysActive
from os import environ, makedirs
from os.path import isdir
from shlex import split as shsplit
from subprocess import PIPE, Popen
import sys

def checkCompiler(log, compileCommand, outDir):
	'''Checks whether compiler can compile anything at all.
	Returns True iff the compiler works.
	'''
	def hello():
		# The most famous program.
		yield '#include <iostream>'
		yield 'int main(int argc, char** argv) {'
		yield '  std::cout << "Hello World!" << std::endl;'
		yield '  return 0;'
		yield '}'
	return tryCompile(log, compileCommand, outDir + '/hello.cc', hello())

def checkFunc(log, compileCommand, outDir, makeName, funcName, headers):
	'''Checks whether the given function is declared by the given headers.
	Returns True iff the function is declared.
	'''
	def takeFuncAddr():
		# Try to include the necessary headers and get the function address.
		for header in headers:
			yield '#include %s' % header
		yield 'void (*f)() = reinterpret_cast<void (*)()>(%s);' % funcName
	return tryCompile(
		log, compileCommand, outDir + '/' + makeName + '.cc', takeFuncAddr()
		)

def checkHeader(log, compileCommand, outDir, makeName, headers):
	'''Checks whether the given headers can be included.
	Returns True iff the headers are available.
	'''
	def includeHeaders():
		# Try to include the headers.
		for header in headers:
			yield '#include %s' % header
	return tryCompile(
		log, compileCommand, outDir + '/' + makeName + '.cc', includeHeaders()
		)

def checkLib(log, compileCommand, linkCommand, outDir, makeName):
	'''Checks whether the given library can be linked against.
	Returns True iff the library is available.
	'''
	return tryLink(
		log, compileCommand, linkCommand, outDir + '/' + makeName + '.cc'
		)

def iterProbeHeader(probeVars):
	yield '// Automatically generated by build system.'
	for name, value in sorted(probeVars.iteritems()):
		if name.startswith('HAVE_'):
			if value:
				yield '#define %s 1' % name
			else:
				yield '// #undef %s' % name

def backtick(log, commandLine):
	'''Run a command and capture what it writes to stdout.
	If the command fails or writes something to stderr, that is logged.
	Returns the captured string, or None if the command failed.
	'''
	# TODO: This is a modified copy-paste from compilers._Command.
	commandParts = shsplit(commandLine)
	env = dict(environ)
	while commandParts:
		if '=' in commandParts[0]:
			name, value = commandParts[0].split('=', 1)
			del commandParts[0]
			env[name] = value
		else:
			break
	else:
		raise ValueError(
			'No command specified in "%s"' % commandLine
			)

	if msysActive():
		commandParts = [ environ['SHELL'], '-c', shjoin(commandParts) ]

	try:
		proc = Popen(
			commandParts, bufsize = -1, env = env,
			stdin = None, stdout = PIPE, stderr = PIPE,
			)
	except OSError, ex:
		print >> log, 'Failed to execute "%s": %s' % (commandLine, ex)
		return None
	stdoutdata, stderrdata = proc.communicate()
	if stderrdata:
		severity = 'warning' if proc.returncode == 0 else 'error'
		log.write('%s executing "%s"\n' % (severity.capitalize(), commandLine))
		# pylint 0.18.0 somehow thinks stdoutdata is a list, not a string.
		# pylint: disable-msg=E1103
		stderrdata = stderrdata.replace('\r', '')
		log.write(stderrdata)
		if not stderrdata.endswith('\n'):
			log.write('\n')
	if proc.returncode == 0:
		return stdoutdata
	else:
		print >> log, 'Execution failed with exit code %d' % proc.returncode
		return None

def evaluateBackticks(log, expression):
	parts = []
	index = 0
	while True:
		start = expression.find('`', index)
		if start == -1:
			parts.append(expression[index : ])
			break
		end = expression.find('`', start + 1)
		if end == -1:
			raise ValueError('Unmatched backtick: %s' % expression)
		parts.append(expression[index : start])
		command = expression[start + 1 : end].strip()
		result = backtick(log, command)
		if result is None:
			raise IOError('Backtick evaluation failed; see log')
		parts.append(result)
		index = end + 1
	return ''.join(parts)

def shjoin(parts):
	def escape(part):
		return ''.join(
			'\\' + ch if ch in '\\ \'"$()[]' else ch
			for ch in part
			)
	return ' '.join(escape(part) for part in parts)

def normalizeWhitespace(expression):
	return shjoin(shsplit(expression))

class TargetSystem(object):

	def __init__(
		self,
		log, compileCommandStr, outDir, probeVars, resolvedVars, customVars,
		disabledLibraries, disabledFuncs, disabledHeaders
		):
		'''Create empty log and result files.
		'''
		self.log = log
		self.compileCommandStr = compileCommandStr
		self.outDir = outDir
		self.probeVars = probeVars
		self.resolvedVars = resolvedVars
		self.customVars = customVars
		self.disabledLibraries = disabledLibraries
		self.disabledFuncs = disabledFuncs
		self.disabledHeaders = disabledHeaders
		self.outMakePath = outDir + '/probed_defs.mk'
		self.outHeaderPath = outDir + '/probed_defs.hh'
		self.outVars = {}

	def checkAll(self):
		'''Run all probes.
		'''
		self.hello()
		for func in self.probeVars['ALL_FUNCS'].split():
			if func in self.disabledFuncs:
				self.disabledFunc(func)
			else:
				self.checkFunc(func)
		for header in self.probeVars['ALL_HEADERS'].split():
			if header in self.disabledHeaders:
				self.disabledHeader(header)
			else:
				self.checkHeader(header)
		for library in self.probeVars['ALL_LIBS'].split():
			if library in self.disabledLibraries:
				self.disabledLibrary(library)
			else:
				self.checkLib(library)

	def printAll(self):
		for package in self.probeVars['ALL_LIBS'].split():
			if package not in self.disabledLibraries:
				for name in ('%s_CFLAGS' % package, '%s_LDFLAGS' % package):
					self.outVars[name] = self.resolvedVars[name]

		def iterVars():
			yield '# Automatically generated by build system.'
			yield '# Non-empty value means found, empty means not found.'
			for item in sorted(self.outVars.iteritems()):
				yield '%s:=%s' % item
		rewriteIfChanged(self.outMakePath, iterVars())

	def printResults(self):
		for line in iterProbeResults(
			self.outVars, self.customVars,
			self.disabledHeaders, self.disabledLibraries
			):
			print line

	def everything(self):
		self.checkAll()
		self.printAll()
		rewriteIfChanged(self.outHeaderPath, iterProbeHeader(self.outVars))
		self.printResults()

	def hello(self):
		'''Check compiler with the most famous program.
		'''
		compileCommand = CompileCommand.fromLine(self.compileCommandStr, '')
		ok = checkCompiler(self.log, compileCommand, self.outDir)
		print >> self.log, 'Compiler %s: %s' % (
			'works' if ok else 'broken',
			compileCommand
			)
		self.outVars['COMPILER'] = str(ok).lower()

	def checkFunc(self, makeName):
		'''Probe for function.
		'''
		compileCommand = CompileCommand.fromLine(self.compileCommandStr, '')
		headers = [ self.probeVars['%s_HEADER' % makeName] ]
		preHeader = self.probeVars.get('%s_PREHEADER' % makeName)
		if preHeader is not None:
			headers.append(preHeader)

		ok = checkFunc(
			self.log, compileCommand, self.outDir,
			makeName, self.probeVars['%s_FUNC' % makeName], headers
			)
		print >> self.log, '%s function: %s' % (
			'Found' if ok else 'Missing',
			makeName
			)
		self.outVars['HAVE_%s' % makeName] = 'true' if ok else ''

	def checkHeader(self, makeName):
		'''Probe for header.
		'''
		flags = self.resolvedVars['%s_CFLAGS' % makeName]
		compileCommand = CompileCommand.fromLine(self.compileCommandStr, flags)
		headers = [ self.probeVars['%s_HEADER' % makeName] ]

		ok = checkHeader(
			self.log, compileCommand, self.outDir, makeName, headers
			)
		print >> self.log, '%s header: %s' % (
			'Found' if ok else 'Missing',
			makeName
			)
		self.outVars['HAVE_%s_H' % makeName] = 'true' if ok else ''

	def checkLib(self, makeName):
		'''Probe for library.
		'''
		flags = self.resolvedVars['%s_LDFLAGS' % makeName]
		compileCommand = CompileCommand.fromLine(self.compileCommandStr, '')
		linkCommand = LinkCommand.fromLine(self.compileCommandStr, flags)

		ok = checkLib(
			self.log, compileCommand, linkCommand, self.outDir, makeName
			)
		print >> self.log, '%s library: %s' % (
			'Found' if ok else 'Missing',
			makeName
			)
		self.outVars['HAVE_%s_LIB' % makeName] = 'yes' if ok else ''

	def disabledFunc(self, func):
		print >> self.log, 'Disabled function: %s' % func
		self.outVars['HAVE_%s' % func] = ''

	def disabledHeader(self, header):
		print >> self.log, 'Disabled header: %s' % header
		self.outVars['HAVE_%s_H' % header] = ''

	def disabledLibrary(self, library):
		print >> self.log, 'Disabled library: %s' % library
		self.outVars['HAVE_%s_LIB' % library] = ''

def main(compileCommandStr, outDir, platform, linkMode, thirdPartyInstall):
	if not isdir(outDir):
		makedirs(outDir)
	log = open(outDir + '/probe.log', 'w')
	print 'Probing target system...'
	print >> log, 'Probing system:'
	try:
		customVars = extractMakeVariables('build/custom.mk')

		# Define default compile/link flags.
		baseVars = dict(customVars)
		baseVars.update({
			'3RDPARTY_INSTALL_DIR': thirdPartyInstall,
			'LINK_FLAGS': '',
			})

		probeDefVars = extractMakeVariables('build/probe_defs.mk', baseVars)
		# Allow the OS specific Makefile to override if necessary.
		probePlatformVars = extractMakeVariables(
			'build/platform-%s.mk' % platform, probeDefVars
			)
		probeVars = dict(
			( key, evalMakeExpr(value, probePlatformVars) )
			for key, value in probePlatformVars.iteritems()
			)

		disabledLibraries = set(probeVars['DISABLED_LIBRARIES'].split())
		disabledFuncs = set()
		disabledHeaders = set()

		if linkMode.startswith('3RD_'):
			# Disable Jack: The CassetteJack feature is not useful for most end
			# users, so do not include Jack in the binary distribution of
			# openMSX.
			disabledLibraries.add('JACK')

			# GLEW header can be <GL/glew.h> or just <glew.h>; the dedicated
			# version we use resides in the "GL" dir, so don't look for the
			# other one, or we might pick up a different version somewhere on
			# the system.
			disabledHeaders.add('GLEW')

		disabledHeaders |= disabledLibraries
		if 'GL' in disabledLibraries:
			disabledHeaders.add('GL_GL')
		if 'GLEW' in disabledLibraries:
			disabledHeaders.add('GL_GLEW')

		systemLibs = set()

		def resolveMode(library, flags):
			'''Resolve probe strings depending on link mode and list of system
			libs.
			'''
			mode = 'SYS_DYN' if library in systemLibs else linkMode
			return probeVars['%s_%s_%s' % (library, flags, mode)]

		resolvedVars = dict(
			# System headers.
			SYS_MMAN_CFLAGS = '',
			SYS_SOCKET_CFLAGS = '',
			# OpenGL is always a system lib.
			GL_CFLAGS = probeVars['GL_CFLAGS'],
			GL_GL_CFLAGS = probeVars['GL_GL_CFLAGS'],
			GL_LDFLAGS = probeVars['GL_LDFLAGS'],
			)
		for package in probeVars['ALL_LIBS'].split():
			if package == 'GL' or package in disabledLibraries:
				continue
			for flagsType in ('CFLAGS', 'LDFLAGS'):
				# TODO: Since for example "sdl-config" is used in more than one
				#       CFLAGS definition, it will be executed multiple times.
				try:
					flags = evaluateBackticks(
						log, resolveMode(package, flagsType)
						)
				except IOError:
					# Executing a lib-config script is expected to fail if the
					# script is not installed.
					# TODO: Report this explicitly in the probe results table.
					flags = ''
				resolvedVars['%s_%s' % (package, flagsType)] = \
					normalizeWhitespace(flags)
		if 'GLEW' not in disabledLibraries:
			resolvedVars['GL_GLEW_CFLAGS'] = resolvedVars['GLEW_CFLAGS']

		TargetSystem(
			log, compileCommandStr, outDir, probeVars, resolvedVars, customVars,
			disabledLibraries, disabledFuncs, disabledHeaders
			).everything()
	finally:
		log.close()

if __name__ == '__main__':
	if len(sys.argv) == 6:
		try:
			main(*sys.argv[1 : ])
		except ValueError, ve:
			print >> sys.stderr, ve
			sys.exit(2)
	else:
		print >> sys.stderr, (
			'Usage: python probe.py '
			'COMPILE OUTDIR OPENMSX_TARGET_OS LINK_MODE 3RDPARTY_INSTALL_DIR'
			)
		sys.exit(2)
