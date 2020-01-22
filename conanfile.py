#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shutil
from conans import ConanFile, CMake, tools


class TesseractConan(ConanFile):
    name = "tesseract"
    version = "3.05.02"
    description = "Tesseract Open Source OCR Engine"
    url = "http://github.com/bincrafters/conan-tesseract"
    author = "Bincrafters <bincrafters@gmail.com>"
    license = "Apache-2.0"
    homepage = "https://github.com/tesseract-ocr/tesseract"
    exports = ["LICENSE.md"]
    exports_sources = ["CMakeLists.txt"]
    generators = "cmake"
    settings = "os", "arch", "compiler", "build_type"
    options = {"shared": [True, False],
               "fPIC": [True, False],
               "with_training": [True, False]}
    default_options = {'shared': False, 'fPIC': True, 'with_training': False}
    _source_subfolder = "source_subfolder"

    requires = "leptonica/1.76.0@bincrafters/stable"

    def source(self):
        tools.get("https://github.com/tesseract-ocr/tesseract/archive/%s.tar.gz" % self.version)
        os.rename("tesseract-" + self.version, self._source_subfolder)
        os.rename(os.path.join(self._source_subfolder, "CMakeLists.txt"),
                  os.path.join(self._source_subfolder, "CMakeListsOriginal.txt"))
        shutil.copy("CMakeLists.txt",
                    os.path.join(self._source_subfolder, "CMakeLists.txt"))
        tools.replace_in_file(os.path.join(self._source_subfolder, "vs2010", "port", "vcsversion.h"),
                              "#define",
                              "// #define")

    def config_options(self):
        if self.settings.os == "Windows":
            self.options.remove("fPIC")
        if self.options.with_training:
            # do not enforce failure and allow user to build with system cairo, pango, fontconfig
            self.output.warn("*** Build with training is not yet supported, continue on your own")

    def system_requirements(self):
        """ Temporary requirement until pkgconfig_installer is introduced """
        if tools.os_info.is_linux and tools.os_info.with_apt:
            installer = tools.SystemPackageTool()
            installer.install('pkg-config')

    def build(self):
        cmake = CMake(self)
        cmake.definitions['BUILD_TRAINING_TOOLS'] = self.options.with_training
        cmake.definitions["BUILD_SHARED_LIBS"] = self.options.shared
        cmake.definitions["STATIC"] = not self.options.shared

        # provide patched lept.pc
        shutil.copy(os.path.join(self.deps_cpp_info['leptonica'].rootpath, 'lib', 'pkgconfig', 'lept.pc'), 'lept.pc')
        tools.replace_prefix_in_pc_file("lept.pc", self.deps_cpp_info['leptonica'].rootpath)

        # VS build uses cmake to locate leptonica
        use_pkg_config = self.settings.compiler != "Visual Studio"
        # use cmake-based configure even for unix
        use_pkg_config = False

        # if static leptonica used with pkg-config, tesseract must use Leptonica_STATIC_LIBRARIES
        # which use static dependencies like jpeg, png etc provided by lept.pc
        if not self.options['leptonica'].shared and use_pkg_config:
            tools.replace_in_file(os.path.join(self._source_subfolder, "CMakeListsOriginal.txt"),
                                  "target_link_libraries       (libtesseract ${Leptonica_LIBRARIES})",
                                  "target_link_libraries       (libtesseract ${Leptonica_STATIC_LIBRARIES})")

        if self.version == "3.05.01":
            # upstream bug: output name is not substituted for tesseract.pc
            # fixed in master but still an issue for stable
            tools.replace_in_file(
                os.path.join(self._source_subfolder, "CMakeListsOriginal.txt"),
                "set_target_properties           (libtesseract PROPERTIES DEBUG_OUTPUT_NAME tesseract${VERSION_MAJOR}${VERSION_MINOR}d)",
                "set_target_properties           (libtesseract PROPERTIES DEBUG_OUTPUT_NAME tesseract${VERSION_MAJOR}${VERSION_MINOR}d)\n"
                "else()\n"
                "set_target_properties           (libtesseract PROPERTIES OUTPUT_NAME tesseract)\n")

        if not use_pkg_config:
            cmake.definitions['Leptonica_DIR'] = self.deps_cpp_info['leptonica'].rootpath

        with tools.environment_append({'PKG_CONFIG_PATH': self.build_folder}) if use_pkg_config else tools.no_op():
            cmake.configure(source_folder=self._source_subfolder)
            cmake.build()
            cmake.install()
            cmake.patch_config_paths()

        self._fix_absolute_paths()

    def _fix_absolute_paths(self):
        # Fix pc file: cmake does not fill libs.private
        if self.settings.compiler != "Visual Studio":
            libs_private = []
            libs_private.extend(['-L'+path for path in self.deps_cpp_info['leptonica'].lib_paths])
            libs_private.extend(['-l'+lib for lib in self.deps_cpp_info['leptonica'].libs])
            path = os.path.join(self.package_folder, 'lib', 'pkgconfig', 'tesseract.pc')
            tools.replace_in_file(path,
                                  'Libs.private:',
                                  'Libs.private: ' + ' '.join(libs_private))

    def package(self):
        self.copy("LICENSE", src=self._source_subfolder, dst="licenses", ignore_case=True, keep_path=False)
        # remove man pages
        shutil.rmtree(os.path.join(self.package_folder, 'share', 'man'), ignore_errors=True)
        # remove binaries
        for ext in ['', '.exe']:
            try:
                os.remove(os.path.join(self.package_folder, 'bin', 'tesseract'+ext))
            except:
                pass


    def package_info(self):
        self.cpp_info.libs = tools.collect_libs(self)
        if self.settings.os == "Linux":
            self.cpp_info.libs.extend(["pthread"])
        if self.settings.compiler == "Visual Studio":
            if not self.options.shared:
                self.cpp_info.libs.append('ws2_32')
